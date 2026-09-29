"""Phase 2 — dedupe Bronze into Silver.

Reads `bronze.products` / `bronze.interactions` in full, runs the
corresponding `sql/silver/*.sql` dedup query through DuckDB, validates the
result, and **overwrites** `silver.products` / `silver.interactions` with
it. Silver is fully recomputed every run rather than merged incrementally —
acceptable at MVP data scale (see `docs/modules/phase-2-lakehouse.md`), and
matches the design choice that Bronze (not Silver) is the append-only
historical layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pyarrow as pa

from jobs.transform.iceberg_writer import get_or_create_table, overwrite
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import (
    bronze_interactions,
    bronze_products,
    silver_interactions,
    silver_products,
)
from reco_mlops_libs.iceberg.validators import validate

SQL_DIR = Path(__file__).resolve().parents[2] / "sql" / "silver"

TABLES = (
    ("bronze_products", bronze_products, "silver_products.sql", silver_products),
    ("bronze_interactions", bronze_interactions, "silver_interactions.sql", silver_interactions),
)


def build_silver_table(catalog, view_name: str, bronze_contract, sql_file: str, silver_contract) -> dict:
    bronze_table = get_or_create_table(catalog, bronze_contract.TABLE_IDENTIFIER, bronze_contract.ICEBERG_SCHEMA)
    bronze_arrow = bronze_table.scan().to_arrow()

    query = (SQL_DIR / sql_file).read_text(encoding="utf-8")
    con = duckdb.connect()
    con.register(view_name, bronze_arrow)
    result_arrow = con.execute(query).to_arrow_table()
    con.close()

    validated = validate(
        result_arrow.to_pandas(),
        silver_contract.PANDERA_SCHEMA,
        table_identifier=silver_contract.TABLE_IDENTIFIER,
    )

    silver_table = get_or_create_table(catalog, silver_contract.TABLE_IDENTIFIER, silver_contract.ICEBERG_SCHEMA)
    overwrite(silver_table, pa.Table.from_pandas(validated, preserve_index=False))

    return {
        "table": silver_contract.TABLE_IDENTIFIER,
        "source_rows": bronze_arrow.num_rows,
        "rows_after_dedup": len(validated),
    }


def main() -> int:
    catalog = get_catalog()
    results = [
        build_silver_table(catalog, view_name, bronze_contract, sql_file, silver_contract)
        for view_name, bronze_contract, sql_file, silver_contract in TABLES
    ]
    print(json.dumps({"silver_build_result": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
