"""Phase 7 — expire old Iceberg snapshots and reclaim their storage
(docs/decisions/0012-iceberg-maintenance-expire-snapshots.md).

`pyiceberg==0.12.0`'s `expire_snapshots()` only commits a
`RemoveSnapshotsUpdate` — it drops snapshots from the table metadata and does
NOT delete any file (measured: after expiring 20 snapshots, all 20 data files
were still in MinIO; Lakekeeper does not delete them either). Left alone,
`compact_iceberg` and the Silver/Gold rewrites would keep every superseded
file forever. So this job does both steps itself:

1. pick snapshots older than the cutoff, never the current snapshot or any
   branch/tag head (PyIceberg refuses to expire those);
2. before committing, collect every file (manifest list, manifests, data
   files) reachable from the snapshots to expire and from the snapshots to
   keep; the difference is what only the expired snapshots used;
3. commit the expiry, then delete exactly that difference. If a delete fails
   the file is only left behind (reported), never a file a kept snapshot
   needs. The commit comes first, so a crash between the two steps leaves
   orphans, not a table pointing at missing files.

Retention trade-off: training logs `iceberg_snapshot_id.<table>` to MLflow
(`reco_mlops_libs.mlflow_utils.logging_helpers`). Once that snapshot is
expired, the exact training data of that model can no longer be re-read by
time travel. The default keeps 7 days (`--older-than-days` /
`EXPIRE_OLDER_THAN_DAYS`); raise it if a model must stay reproducible longer.

Do not run this concurrently with a writer on the same table.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

from pyiceberg.table import Snapshot, Table
from pyiceberg.table.refs import SnapshotRefType

from jobs.transform.iceberg_writer import get_or_create_table
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import (
    bronze_interactions,
    bronze_products,
    gold_item_features,
    gold_user_features,
    silver_interactions,
    silver_products,
)

TABLES = (
    bronze_products,
    bronze_interactions,
    silver_products,
    silver_interactions,
    gold_item_features,
    gold_user_features,
)

DEFAULT_OLDER_THAN_DAYS = 7


def snapshot_files(table: Table, snapshot: Snapshot) -> set[str]:
    """Every file one snapshot needs: its manifest list, its manifests and
    the data/delete files those manifests still list (deleted entries are
    not needed by the snapshot)."""
    io = table.io
    paths = {snapshot.manifest_list}
    for manifest in snapshot.manifests(io):
        paths.add(manifest.manifest_path)
        for entry in manifest.fetch_manifest_entry(io, discard_deleted=True):
            paths.add(entry.data_file.file_path)
    return paths


def delete_file(table: Table, path: str) -> None:
    """Delete ONE object with a single-key DeleteObject. `table.io.delete()`
    goes through s3fs `rm`, which batches into a multi-object
    `POST /<bucket>?delete`; Lakekeeper's remote signer answers that with
    `SignError 400` (measured), so every delete would fail. `rm_file` issues
    the plain `DELETE /<bucket>/<key>` that the signer accepts."""
    scheme = path.split("://", 1)[0]
    table.io.get_fs(scheme).rm_file(path)


def _protected_ids(table: Table) -> set[int]:
    return {
        ref.snapshot_id
        for ref in table.metadata.refs.values()
        if ref.snapshot_ref_type in (SnapshotRefType.BRANCH, SnapshotRefType.TAG)
    }


def expire_loaded_table(table: Table, identifier: str, older_than: datetime, *, dry_run: bool = False) -> dict:
    """Expire snapshots older than `older_than` on one loaded table and delete
    the files only they used. Takes a `Table` so integration tests can drive it
    on `*.it_test_*` tables."""
    cutoff_ms = int(older_than.timestamp() * 1000)
    protected = _protected_ids(table)
    snapshots = table.snapshots()
    expire = [s for s in snapshots if s.timestamp_ms < cutoff_ms and s.snapshot_id not in protected]
    keep = [s for s in snapshots if s not in expire]

    result = {
        "table": identifier,
        "snapshots_before": len(snapshots),
        "snapshots_expired": len(expire),
        "dry_run": dry_run,
    }
    if not expire:
        return {**result, "snapshots_after": len(snapshots), "files_deleted": 0, "files_failed": 0}

    expired_files: set[str] = set()
    for snapshot in expire:
        expired_files |= snapshot_files(table, snapshot)
    kept_files: set[str] = set()
    for snapshot in keep:
        kept_files |= snapshot_files(table, snapshot)
    orphans = sorted(expired_files - kept_files)

    if dry_run:
        return {**result, "snapshots_after": len(snapshots), "files_to_delete": len(orphans)}

    table.maintenance.expire_snapshots().by_ids([s.snapshot_id for s in expire]).commit()
    table.refresh()

    failed = []
    for path in orphans:
        try:
            delete_file(table, path)
        except Exception as error:  # a leftover file is harmless; report it, keep going
            failed.append({"path": path, "error": f"{type(error).__name__}: {error}"})
    return {
        **result,
        "snapshots_after": len(table.snapshots()),
        "files_deleted": len(orphans) - len(failed),
        "files_failed": len(failed),
        **({"failures": failed[:5]} if failed else {}),
    }


def expire_table(catalog, contract, older_than: datetime, *, dry_run: bool = False) -> dict:
    table = get_or_create_table(catalog, contract.TABLE_IDENTIFIER, contract.ICEBERG_SCHEMA)
    return expire_loaded_table(table, contract.TABLE_IDENTIFIER, older_than, dry_run=dry_run)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expire old Iceberg snapshots and delete files only they used.")
    parser.add_argument(
        "--older-than-days", type=float,
        default=float(os.getenv("EXPIRE_OLDER_THAN_DAYS", DEFAULT_OLDER_THAN_DAYS)),
        help="expire snapshots older than this many days (the current snapshot is never expired)",
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would be deleted, change nothing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.older_than_days < 0:
        raise ValueError("--older-than-days must be >= 0")
    older_than = datetime.now(timezone.utc) - timedelta(days=args.older_than_days)
    catalog = get_catalog()
    results = [expire_table(catalog, contract, older_than, dry_run=args.dry_run) for contract in TABLES]
    print(json.dumps({"expire_result": results, "older_than": older_than.isoformat()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
