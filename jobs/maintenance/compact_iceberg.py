"""Phase 7 — compact Bronze Iceberg tables (deferred from Phase 2, see
`docs/modules/phase-2-lakehouse.md` mục "việc còn lại").

Only Bronze needs this: it is the sole append-only table (every Phase 1
extract run adds a new data file, see `jobs/transform/build_bronze.py`), so
its small-file count grows without bound over time. Silver/Gold already
`overwrite()` their entire content every run (`jobs/transform/build_silver.py`
/`build_gold.py`) — each run already produces a small, fresh set of files,
so there is nothing to compact there.

`pyiceberg==0.12.0` (pinned in `libs/pyproject.toml`) exposes
`table.maintenance` but that only wraps `expire_snapshots` in this version
— no native `rewrite_data_files`/compaction action yet (checked directly
against the installed version before writing this). So compaction here is
done the same way Silver/Gold already achieve it: read the table's full
current content and `overwrite()` it with itself. This produces a new
snapshot with far fewer, larger files while leaving every prior snapshot
(and the files behind it) intact and still time-travel-able — real Iceberg
compaction semantics, not a workaround. Snapshot *expiration* (reclaiming
the now-superseded small files' storage) is intentionally left out of this
pass — see `docs/modules/phase-7-monitoring.md` "việc còn lại".
"""

from __future__ import annotations

import json

from jobs.transform.iceberg_writer import get_or_create_table, overwrite
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import bronze_interactions, bronze_products

TABLES = (bronze_products, bronze_interactions)


def compact_table(catalog, contract) -> dict:
    table = get_or_create_table(catalog, contract.TABLE_IDENTIFIER, contract.ICEBERG_SCHEMA)
    before_files = sum(1 for _ in table.scan().plan_files())
    if before_files <= 1:
        return {"table": contract.TABLE_IDENTIFIER, "skipped": "already 1 file or empty", "files": before_files}

    data = table.scan().to_arrow()
    overwrite(table, data)

    table.refresh()
    after_files = sum(1 for _ in table.scan().plan_files())
    return {
        "table": contract.TABLE_IDENTIFIER,
        "rows": data.num_rows,
        "files_before": before_files,
        "files_after": after_files,
    }


def main() -> int:
    catalog = get_catalog()
    results = [compact_table(catalog, contract) for contract in TABLES]
    print(json.dumps({"compact_result": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
