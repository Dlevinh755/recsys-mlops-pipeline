"""Phase 7 — compact Bronze and Silver Iceberg tables (deferred from Phase 2,
see `docs/modules/phase-2-lakehouse.md` mục "việc còn lại"; Silver added
2026-11-24, docs/decisions/0012-iceberg-maintenance-expire-snapshots.md).

Bronze is append-only (every Phase 1 extract run adds a new data file, see
`jobs/transform/build_bronze.py`), so its small-file count grows without
bound. Silver has been an incremental upsert since ADR 0010: every run with
new data adds small files (measured on a test table: 6 incremental runs left
13 live data files, 2 per run). Gold `overwrite()`s its entire content every
run (`jobs/transform/build_gold.py`), so it is already one fresh set of files
and is not compacted.

`pyiceberg==0.12.0` (pinned in `libs/pyproject.toml`) has no native
`rewrite_data_files` action (`table.maintenance` only wraps
`expire_snapshots`). So compaction here reads the table's full current
content and `overwrite()`s it with itself: one new snapshot with far fewer,
larger files. The overwrite leaves table *properties* alone, so Silver's
`reco.silver.watermark_ingested_at` survives (asserted in
`tests/integration/test_maintenance.py`). Prior snapshots (and the files
behind them) stay intact and time-travel-able until
`jobs/maintenance/expire_snapshots.py` removes them.

Do not run this concurrently with `build_silver` (the DAGs run one after
another): an overwrite that races an upsert would drop one of the two.
"""

from __future__ import annotations

import json

from pyiceberg.table import Table

from jobs.transform.iceberg_writer import get_or_create_table, overwrite
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import (
    bronze_interactions,
    bronze_products,
    silver_interactions,
    silver_products,
)

TABLES = (bronze_products, bronze_interactions, silver_products, silver_interactions)


def compact_loaded_table(table: Table, identifier: str) -> dict:
    """Compact one already-loaded table. Takes a `Table` (not a contract) so
    integration tests can run it on `*.it_test_*` tables."""
    before_files = sum(1 for _ in table.scan().plan_files())
    if before_files <= 1:
        return {"table": identifier, "skipped": "already 1 file or empty", "files": before_files}

    data = table.scan().to_arrow()
    overwrite(table, data)

    table.refresh()
    after_files = sum(1 for _ in table.scan().plan_files())
    return {
        "table": identifier,
        "rows": data.num_rows,
        "files_before": before_files,
        "files_after": after_files,
    }


def compact_table(catalog, contract) -> dict:
    table = get_or_create_table(catalog, contract.TABLE_IDENTIFIER, contract.ICEBERG_SCHEMA)
    return compact_loaded_table(table, contract.TABLE_IDENTIFIER)


def main() -> int:
    catalog = get_catalog()
    results = [compact_table(catalog, contract) for contract in TABLES]
    print(json.dumps({"compact_result": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
