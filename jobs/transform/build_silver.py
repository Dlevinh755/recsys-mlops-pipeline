"""Phase 2 — dedupe Bronze into Silver, incrementally.

Each run processes only the Bronze rows ingested after the watermark stored
in the Silver table's own properties (`reco.silver.watermark_ingested_at` =
max `_ingested_at` already handled), dedups that delta with the unchanged
`sql/silver/*.sql` (DuckDB), drops rows older than what Silver already has
for the same key, and upserts the rest. Data and watermark commit in one
Iceberg transaction. The result is equivalent to recomputing everything —
`--full-refresh` does exactly that and is the escape hatch (schema/SQL
change, suspected drift, or a duplicate-key error from the upsert).

No watermark yet (first run, or `--full-refresh`) means "full": read all
Bronze, overwrite Silver, then set the watermark. Design and trade-offs:
docs/decisions/0010-silver-incremental.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
from pyiceberg.expressions import In
from pyiceberg.table import Table

from jobs.transform.iceberg_writer import (
    SILVER_WATERMARK_PROPERTY,
    format_watermark,
    get_or_create_table,
    overwrite_with_watermark,
    read_watermark,
    upsert_with_watermark,
)
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import (
    bronze_interactions,
    bronze_products,
    silver_interactions,
    silver_products,
)
from reco_mlops_libs.iceberg.validators import validate

SQL_DIR = Path(__file__).resolve().parents[2] / "sql" / "silver"

# (bronze view name used by the SQL, bronze contract, sql file, silver contract, join key)
TABLES = (
    ("bronze_products", bronze_products, "silver_products.sql", silver_products, "product_id"),
    ("bronze_interactions", bronze_interactions, "silver_interactions.sql", silver_interactions, "interaction_id"),
)

INGESTED_AT = "_ingested_at"


def _max_ingested_at(bronze_arrow: pa.Table) -> str | None:
    latest = pc.max(bronze_arrow[INGESTED_AT]).as_py()
    return format_watermark(latest) if latest is not None else None


def _dedup(view_name: str, sql_file: str, bronze_arrow: pa.Table) -> pa.Table:
    """Run the unchanged Silver SQL over `bronze_arrow` (full Bronze or a delta)."""
    con = duckdb.connect()
    try:
        con.register(view_name, bronze_arrow)
        return con.execute((SQL_DIR / sql_file).read_text(encoding="utf-8")).to_arrow_table()
    finally:
        con.close()


def _drop_stale(deduped: pa.Table, silver_table: Table, key: str) -> pa.Table:
    """Keep delta rows that are new or strictly newer (`updated_at`) than the
    Silver row with the same key. `Table.upsert` itself overwrites any
    matching row that differs, even an older one — it never compares
    `updated_at` — so a late-arriving old version must be filtered out here."""
    if deduped.num_rows == 0 or silver_table.current_snapshot() is None:
        return deduped
    keys = deduped[key].to_pylist()
    existing = silver_table.scan(
        row_filter=In(key, keys),
        selected_fields=(key, "updated_at"),
    ).to_arrow()
    if existing.num_rows == 0:
        return deduped
    con = duckdb.connect()
    try:
        con.register("delta", deduped)
        con.register("existing", existing)
        return con.execute(
            f"SELECT d.* FROM delta d LEFT JOIN existing e ON d.{key} = e.{key} "
            "WHERE e.updated_at IS NULL OR d.updated_at > e.updated_at"
        ).to_arrow_table()
    finally:
        con.close()


def sync_silver_table(
    bronze_table: Table,
    silver_table: Table,
    view_name: str,
    sql_file: str,
    silver_contract,
    key: str,
    *,
    full_refresh: bool = False,
) -> dict:
    """Bring `silver_table` up to date with `bronze_table`. Takes `Table`
    objects (not identifiers) so integration tests can drive it on
    `*.it_test_*` tables."""
    watermark_before = read_watermark(silver_table)
    full = full_refresh or watermark_before is None
    mode = "full" if full else "incremental"

    if full:
        bronze_arrow = bronze_table.scan().to_arrow()
    else:
        bronze_arrow = bronze_table.scan(row_filter=f"{INGESTED_AT} > '{watermark_before}'").to_arrow()

    result = {
        "table": silver_contract.TABLE_IDENTIFIER,
        "mode": mode,
        "bronze_rows_scanned": bronze_arrow.num_rows,
        "rows_inserted": 0,
        "rows_updated": 0,
        "rows_upserted": 0,
        "rows_skipped_stale": 0,
        "rows_after_dedup": 0,
        "watermark_before": watermark_before,
        "watermark_after": watermark_before,
    }

    if not full and bronze_arrow.num_rows == 0:
        return result  # nothing new: no commit, no empty snapshot

    deduped = _dedup(view_name, sql_file, bronze_arrow)
    watermark_after = _max_ingested_at(bronze_arrow)

    if full:
        to_write = deduped
    else:
        to_write = _drop_stale(deduped, silver_table, key)
        result["rows_skipped_stale"] = deduped.num_rows - to_write.num_rows

    validated = validate(
        to_write.to_pandas(), silver_contract.PANDERA_SCHEMA,
        table_identifier=silver_contract.TABLE_IDENTIFIER,
    )
    data = pa.Table.from_pandas(validated, preserve_index=False)
    result["rows_after_dedup"] = len(validated)

    if full:
        overwrite_with_watermark(silver_table, data, watermark_after)
        result["rows_inserted"] = len(validated)  # full rewrite: everything counts as inserted
    else:
        upserted = upsert_with_watermark(silver_table, data, [key], watermark_after)
        result["rows_inserted"] = upserted.rows_inserted
        result["rows_updated"] = upserted.rows_updated
    result["rows_upserted"] = result["rows_inserted"] + result["rows_updated"]
    result["watermark_after"] = watermark_after or watermark_before
    return result


def build_silver_table(
    catalog, view_name: str, bronze_contract, sql_file: str, silver_contract, key: str,
    *, full_refresh: bool = False,
) -> dict:
    bronze_table = get_or_create_table(catalog, bronze_contract.TABLE_IDENTIFIER, bronze_contract.ICEBERG_SCHEMA)
    silver_table = get_or_create_table(catalog, silver_contract.TABLE_IDENTIFIER, silver_contract.ICEBERG_SCHEMA)
    return sync_silver_table(
        bronze_table, silver_table, view_name, sql_file, silver_contract, key, full_refresh=full_refresh
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Silver (incremental by default).")
    parser.add_argument(
        "--full-refresh", action="store_true",
        help=f"ignore the watermark ({SILVER_WATERMARK_PROPERTY}), recompute Silver from all of Bronze",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog = get_catalog()
    results = [
        build_silver_table(catalog, view_name, bronze_contract, sql_file, silver_contract, key,
                           full_refresh=args.full_refresh)
        for view_name, bronze_contract, sql_file, silver_contract, key in TABLES
    ]
    print(json.dumps({"silver_build_result": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
