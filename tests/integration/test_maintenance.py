"""Integration tests for Iceberg maintenance
(docs/decisions/0012-iceberg-maintenance-expire-snapshots.md): Silver
compaction and snapshot expiry that really frees storage.

Run against the real Lakekeeper + MinIO, on `*.it_test_mt_*` tables only:

    docker compose --profile jobs run --rm --entrypoint python transform-job -m pytest /app/tests/integration -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from pyiceberg.exceptions import SignError

from jobs.maintenance.compact_iceberg import compact_loaded_table
from jobs.maintenance.expire_snapshots import expire_loaded_table, snapshot_files
from jobs.transform.iceberg_writer import SILVER_WATERMARK_PROPERTY, read_watermark
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import bronze_interactions, silver_interactions
from tests.integration.test_silver_incremental import _fresh, add, rows, sync, t

BRONZE = "bronze.it_test_mt_interactions"
SILVER = "silver.it_test_mt_interactions"
SILVER_REF = "silver.it_test_mt_interactions_ref"


@pytest.fixture()
def tables():
    catalog = get_catalog()
    created = {
        "bronze": _fresh(catalog, BRONZE, bronze_interactions.ICEBERG_SCHEMA),
        "silver": _fresh(catalog, SILVER, silver_interactions.ICEBERG_SCHEMA),
        "ref": _fresh(catalog, SILVER_REF, silver_interactions.ICEBERG_SCHEMA),
    }
    yield created
    for name in (BRONZE, SILVER, SILVER_REF):
        if catalog.table_exists(name):
            catalog.purge_table(name)


def grow_silver(tables, runs: int = 5) -> None:
    """Initial load plus `runs` incremental syncs, each inserting 3 rows and
    updating one older row — the pattern that leaves small files behind."""
    b, s = tables["bronze"], tables["silver"]
    add(b, [{"id": i, "upd": 1} for i in range(1, 21)], t(10))
    sync(b, s)
    for k in range(1, runs + 1):
        add(
            b,
            [{"id": 100 * k + i, "upd": 1} for i in range(3)] + [{"id": k, "upd": 10 + k, "rating": Decimal("1.0")}],
            t(10 + 10 * k),
        )
        sync(b, s)


def reload(table):
    return get_catalog().load_table(table.name())


def live_files(table) -> int:
    return sum(1 for _ in table.scan().plan_files())


def all_files(table) -> set[str]:
    paths: set[str] = set()
    for snapshot in table.snapshots():
        paths |= snapshot_files(table, snapshot)
    return paths


def exists(table, path: str) -> bool:
    """`exists()` on a *deleted* key makes s3fs fall back to a prefix listing,
    which Lakekeeper's signer refuses (SignError 400). So SignError here means
    "no such object"; a present object answers its HEAD request normally."""
    try:
        return table.io.new_input(path).exists()
    except SignError:
        return False


def far_future() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=5)


def test_compact_silver_merges_files_and_keeps_data_and_watermark(tables):
    b, s = tables["bronze"], tables["silver"]
    grow_silver(tables)
    s = reload(s)
    assert live_files(s) > 1, "incremental runs should have left several files"
    rows_before = rows(s)
    watermark_before = read_watermark(s)

    result = compact_loaded_table(s, SILVER)
    s = reload(s)

    assert result["files_before"] > result["files_after"] == 1
    assert rows(s) == rows_before
    assert read_watermark(s) == watermark_before
    assert s.properties[SILVER_WATERMARK_PROPERTY] == watermark_before

    # the next incremental run still works from the preserved watermark and
    # equals a full recompute of the same Bronze
    add(b, [{"id": 900, "upd": 1}, {"id": 2, "upd": 99, "rating": Decimal("2.0")}], t(500))
    run = sync(b, s)
    assert run["mode"] == "incremental" and run["bronze_rows_scanned"] == 2
    ref = tables["ref"]
    sync(b, ref, full_refresh=True)
    assert rows(reload(s)) == rows(reload(ref))


def test_compact_is_a_noop_for_single_file_table(tables):
    add(tables["bronze"], [{"id": 1, "upd": 1}], t(10))
    sync(tables["bronze"], tables["silver"])
    result = compact_loaded_table(reload(tables["silver"]), SILVER)
    assert "skipped" in result


@pytest.mark.parametrize("compact_first", [True, False], ids=["after-compact", "shared-files"])
def test_expire_frees_files_only_old_snapshots_used(tables, compact_first):
    """`shared-files`: without compaction the current snapshot still uses data
    files that older snapshots also list, so deleting everything an expired
    snapshot lists would destroy live data — only the difference may go."""
    s = tables["silver"]
    grow_silver(tables)
    s = reload(s)
    if compact_first:
        compact_loaded_table(s, SILVER)
        s = reload(s)
    rows_before = rows(s)
    current = s.current_snapshot().snapshot_id
    needed_by_current = snapshot_files(s, s.current_snapshot())
    everything_before = all_files(s)
    assert len(s.snapshots()) > 1 and len(everything_before) > len(needed_by_current)

    result = expire_loaded_table(s, SILVER, far_future())
    s = reload(s)

    assert result["files_failed"] == 0
    assert result["files_deleted"] == len(everything_before - needed_by_current)
    assert [x.snapshot_id for x in s.snapshots()] == [current]
    assert rows(s) == rows_before  # current data still readable
    assert all(exists(s, p) for p in needed_by_current)
    assert not any(exists(s, p) for p in everything_before - needed_by_current)
    assert read_watermark(s) is not None


def test_expire_dry_run_changes_nothing(tables):
    grow_silver(tables, runs=2)
    s = reload(tables["silver"])
    snapshots_before = {x.snapshot_id for x in s.snapshots()}
    files_before = all_files(s)

    result = expire_loaded_table(s, SILVER, far_future(), dry_run=True)
    s = reload(s)

    assert result["dry_run"] and result["files_to_delete"] > 0
    assert {x.snapshot_id for x in s.snapshots()} == snapshots_before
    assert all(exists(s, p) for p in files_before)


def test_expire_keeps_snapshots_newer_than_cutoff(tables):
    grow_silver(tables, runs=2)
    s = reload(tables["silver"])
    count = len(s.snapshots())
    result = expire_loaded_table(s, SILVER, datetime.now(timezone.utc) - timedelta(days=1))
    assert result["snapshots_expired"] == 0 and result["files_deleted"] == 0
    assert len(reload(s).snapshots()) == count
