"""Offline sources for Feast — Parquet snapshots on MinIO written by
`jobs/materialize/export_to_parquet.py` (Iceberg cannot be read reliably by
Feast yet, see `docs/de-xuat-trien-khai.md` Phase 3).

`timestamp_field` uses `computed_at` — the batch-recompute timestamp stamped
by `jobs/transform/build_gold.py` / `jobs/features/build_user_features.py`,
not an event timestamp. Both Gold tables are fully recomputed every run
(no historical rows to pick among), so a single flat timestamp is enough
for Feast's point-in-time join to always pick "the current snapshot".
"""

from feast import FileSource
from feast.data_format import ParquetFormat

item_features_source = FileSource(
    name="item_features_source",
    path="s3://feast-offline-store/gold_item_features.parquet",
    file_format=ParquetFormat(),
    timestamp_field="computed_at",
)

user_features_source = FileSource(
    name="user_features_source",
    path="s3://feast-offline-store/gold_user_features.parquet",
    file_format=ParquetFormat(),
    timestamp_field="computed_at",
)
