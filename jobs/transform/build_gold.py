"""Phase 2 — aggregate Silver into Gold (`gold.item_features`).

Same recompute-every-run approach as `build_silver.py`. Per-user features
(`gold.user_features`) are Phase 3 scope (`jobs/features/`), not built
here — see `docs/de-xuat-trien-khai.md` Phase 3.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pyarrow as pa

from jobs.transform.iceberg_writer import get_or_create_table, overwrite
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import (
    gold_item_features,
    silver_interactions,
    silver_products,
)
from reco_mlops_libs.iceberg.validators import validate

SQL_FILE = Path(__file__).resolve().parents[2] / "sql" / "gold" / "gold_item_features.sql"


def main() -> int:
    catalog = get_catalog()

    products_table = get_or_create_table(catalog, silver_products.TABLE_IDENTIFIER, silver_products.ICEBERG_SCHEMA)
    interactions_table = get_or_create_table(
        catalog, silver_interactions.TABLE_IDENTIFIER, silver_interactions.ICEBERG_SCHEMA
    )

    con = duckdb.connect()
    con.register("silver_products", products_table.scan().to_arrow())
    con.register("silver_interactions", interactions_table.scan().to_arrow())
    result_arrow = con.execute(SQL_FILE.read_text(encoding="utf-8")).to_arrow_table()
    con.close()

    validated = validate(
        result_arrow.to_pandas(),
        gold_item_features.PANDERA_SCHEMA,
        table_identifier=gold_item_features.TABLE_IDENTIFIER,
    )

    gold_table = get_or_create_table(catalog, gold_item_features.TABLE_IDENTIFIER, gold_item_features.ICEBERG_SCHEMA)
    overwrite(gold_table, pa.Table.from_pandas(validated, preserve_index=False))

    result = {"table": gold_item_features.TABLE_IDENTIFIER, "rows": len(validated)}
    print(json.dumps({"gold_build_result": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
