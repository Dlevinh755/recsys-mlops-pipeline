"""Phase 3 — export `gold.item_features` / `gold.user_features` (Iceberg)
to a Parquet snapshot on MinIO for Feast's offline store.

Feast does not read Iceberg tables reliably yet (see
`docs/bao-cao-ky-thuat.md` mục 6 / `de-xuat-trien-khai.md` Phase 3), so this
job is the required bridge: full snapshot, overwritten every run — same
recompute-every-run approach as the Gold tables themselves.

Snapshot goes to MinIO (`FEAST_S3_BUCKET`), not local disk — see
`docs/decisions/0003-extract-writes-directly-to-minio.md` for why local
staging between two separately-run jobs is the wrong default here.
"""

from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq
import s3fs

from jobs.transform.iceberg_writer import get_or_create_table
from reco_mlops_libs.common.env import require_env
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import gold_item_features, gold_user_features

SNAPSHOT_TARGETS = (
    (gold_item_features, "gold_item_features.parquet"),
    (gold_user_features, "gold_user_features.parquet"),
)


def _feast_offline_filesystem() -> s3fs.S3FileSystem:
    endpoint = require_env("FEAST_S3_ENDPOINT")
    return s3fs.S3FileSystem(
        key=require_env("FEAST_S3_ACCESS_KEY"),
        secret=require_env("FEAST_S3_SECRET_KEY"),
        client_kwargs={"endpoint_url": endpoint},
        use_ssl=endpoint.startswith("https://"),
    )


def _decimal_columns_to_float64(table: pa.Table) -> pa.Table:
    """Feast has no Decimal feature type — cast any decimal128 column (e.g.
    `price`) to float64 before writing the Feast-facing Parquet snapshot.
    The Iceberg table itself keeps the exact Decimal type; this cast only
    affects this snapshot copy.
    """
    for index, field in enumerate(table.schema):
        if pa.types.is_decimal(field.type):
            table = table.set_column(index, field.name, table.column(index).cast(pa.float64()))
    return table


def main() -> int:
    catalog = get_catalog()
    bucket = require_env("FEAST_S3_BUCKET")
    fs = _feast_offline_filesystem()

    results = []
    for contract, filename in SNAPSHOT_TARGETS:
        table = get_or_create_table(catalog, contract.TABLE_IDENTIFIER, contract.ICEBERG_SCHEMA)
        arrow_table = _decimal_columns_to_float64(table.scan().to_arrow())
        path = f"{bucket}/{filename}"
        pq.write_table(arrow_table, path, filesystem=fs)
        results.append({"table": contract.TABLE_IDENTIFIER, "path": f"s3://{path}", "rows": arrow_table.num_rows})

    print(json.dumps({"export_to_parquet_result": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
