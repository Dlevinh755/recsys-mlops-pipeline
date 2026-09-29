"""Phase 3 — build `gold.user_features` (recent-item sequence per user).

Reads `silver.interactions` in full, runs `sql/gold/gold_user_features.sql`
through DuckDB to keep each user's last `MAX_SEQUENCE_LENGTH` items in
chronological order, validates, and **overwrites**
`gold.user_features` — same recompute-every-run approach as
`jobs/transform/build_silver.py`/`build_gold.py` (see
`docs/modules/phase-2-lakehouse.md`).

This table (not a flat aggregate) is the primary input for the GRU
sequence model — see `docs/decisions/0004-gru-sequence-model-thay-lightgbm.md`.
Users with zero interactions never appear here (no cold-start row); serving
handles that case via fallback, not via an empty sequence placeholder.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pyarrow as pa

from jobs.transform.iceberg_writer import get_or_create_table, overwrite
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import gold_user_features, silver_interactions
from reco_mlops_libs.iceberg.validators import validate

SQL_FILE = Path(__file__).resolve().parents[2] / "sql" / "gold" / "gold_user_features.sql"


def main() -> int:
    catalog = get_catalog()

    interactions_table = get_or_create_table(
        catalog, silver_interactions.TABLE_IDENTIFIER, silver_interactions.ICEBERG_SCHEMA
    )

    con = duckdb.connect()
    con.register("silver_interactions", interactions_table.scan().to_arrow())
    result_arrow = con.execute(SQL_FILE.read_text(encoding="utf-8")).to_arrow_table()
    con.close()

    validated = validate(
        result_arrow.to_pandas(),
        gold_user_features.PANDERA_SCHEMA,
        table_identifier=gold_user_features.TABLE_IDENTIFIER,
    )

    user_features_table = get_or_create_table(
        catalog, gold_user_features.TABLE_IDENTIFIER, gold_user_features.ICEBERG_SCHEMA
    )
    overwrite(user_features_table, pa.Table.from_pandas(validated, preserve_index=False))

    result = {"table": gold_user_features.TABLE_IDENTIFIER, "rows": len(validated)}
    print(json.dumps({"user_features_build_result": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
