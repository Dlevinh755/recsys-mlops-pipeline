"""Phase 2 — append Phase 1 extract output into Bronze Iceberg tables.

Reads the Parquet dataset `jobs/extract/run.py` writes straight to MinIO
(bucket `EXTRACT_S3_BUCKET`, dataset `staging`, see
docs/decisions/0003-extract-writes-directly-to-minio.md) and appends the
rows not yet committed into `bronze.products` / `bronze.interactions`.

Idempotency: every row `dlt` writes carries `_dlt_load_id` — the id of the
`pipeline.run()` load package it came from, unique per successful dlt run.
That value is stamped into Bronze as `_extraction_run_id` (same column name
as before, new source of values). Before writing, we ask Iceberg (not a side
bookkeeping table) which load ids are already present — see
`iceberg_writer.committed_run_ids`. Re-running this job with no new dlt
loads commits zero rows.

See docs/decisions/0006-dlt-native-incremental-extract.md for why this
replaced a Postgres `pipeline.extraction_runs` table keyed by run *status*:
a run that failed partway could durably write batches to MinIO that then
never got loaded into Bronze, because the parent run's status never reached
'succeeded'. A `_dlt_load_id` has no such gap — it only exists on rows from
a load package dlt actually finished writing to the destination.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import s3fs

from jobs.transform.iceberg_writer import (
    append,
    cast_to_arrow_schema,
    committed_run_ids,
    fill_missing_optional_columns,
    get_or_create_table,
)
from reco_mlops_libs.common.env import require_env
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import bronze_interactions, bronze_products
from reco_mlops_libs.iceberg.validators import validate

_DLT_LOAD_ID_COLUMN = "_dlt_load_id"
_DLT_ID_COLUMN = "_dlt_id"
_LINEAGE_COLUMNS = ("_extraction_run_id", "_ingested_at")

SOURCES = (
    ("products", bronze_products),
    ("interactions", bronze_interactions),
)


def extract_staging_filesystem() -> s3fs.S3FileSystem:
    endpoint = require_env("EXTRACT_S3_ENDPOINT")
    return s3fs.S3FileSystem(
        key=require_env("EXTRACT_S3_ACCESS_KEY"),
        secret=require_env("EXTRACT_S3_SECRET_KEY"),
        client_kwargs={"endpoint_url": endpoint},
        use_ssl=endpoint.startswith("https://"),
    )


def _staging_path(source_name: str) -> str:
    bucket = require_env("EXTRACT_S3_BUCKET")
    return f"{bucket}/staging/{source_name}"


def _raw_staging_schema(contract) -> pa.Schema:
    """The schema every staging parquet file should be normalized to before
    concatenation: the contract's own source columns (everything except the
    two Bronze-only lineage columns, which don't exist yet at this raw
    staging stage) plus dlt's two bookkeeping columns.
    """
    iceberg_arrow = contract.ICEBERG_SCHEMA.as_arrow()
    source_fields = [field for field in iceberg_arrow if field.name not in _LINEAGE_COLUMNS]
    return pa.schema([
        *source_fields,
        pa.field(_DLT_LOAD_ID_COLUMN, pa.string()),
        pa.field(_DLT_ID_COLUMN, pa.string()),
    ])


def _read_staging_table(fs: s3fs.S3FileSystem, staging_path: str, contract) -> pa.Table:
    """Read every dlt-written parquet file under `staging_path` and union
    them into one Arrow table.

    Reads file-by-file rather than one `ds.dataset(dir).to_table()` call:
    different pages/runs can produce files with slightly different schemas
    — dlt drops an optional column entirely from a file when every value in
    that file happens to be NULL (e.g. no `rating` events in one page),
    which broke `pa.concat_tables` before (see
    docs/modules/phase-2-lakehouse.md mục 4.1/4.2). Normalizing every file to
    one canonical schema first avoids that recurring here.
    """
    files = sorted(path for path in fs.find(staging_path) if path.endswith(".parquet"))
    if not files:
        return pa.table({}, schema=_raw_staging_schema(contract))

    canonical_schema = _raw_staging_schema(contract)
    tables = []
    for file_path in files:
        file_table = ds.dataset(file_path, filesystem=fs, format="parquet").to_table()
        file_table = fill_missing_optional_columns(file_table, contract.ICEBERG_SCHEMA)
        tables.append(cast_to_arrow_schema(file_table, canonical_schema))
    return pa.concat_tables(tables)


def build_bronze_table(fs: s3fs.S3FileSystem, source_name: str, contract) -> dict:
    catalog = get_catalog()
    table = get_or_create_table(catalog, contract.TABLE_IDENTIFIER, contract.ICEBERG_SCHEMA)
    already_committed = committed_run_ids(table)

    staging_path = _staging_path(source_name)
    if not fs.exists(staging_path):
        return {"table": contract.TABLE_IDENTIFIER, "rows_committed": 0, "load_ids_committed": []}

    staged = _read_staging_table(fs, staging_path, contract)
    if staged.num_rows == 0:
        return {"table": contract.TABLE_IDENTIFIER, "rows_committed": 0, "load_ids_committed": []}

    if already_committed:
        already_seen = pc.is_in(
            staged[_DLT_LOAD_ID_COLUMN], value_set=pa.array(sorted(already_committed))
        )
        staged = staged.filter(pc.invert(already_seen))
    if staged.num_rows == 0:
        return {"table": contract.TABLE_IDENTIFIER, "rows_committed": 0, "load_ids_committed": []}

    load_ids = sorted(set(staged[_DLT_LOAD_ID_COLUMN].to_pylist()))
    staged = staged.append_column(
        "_extraction_run_id", staged[_DLT_LOAD_ID_COLUMN].cast(pa.string())
    )
    ingested_at = pa.array(
        [datetime.now(timezone.utc)] * staged.num_rows, type=pa.timestamp("us", tz="UTC")
    )
    staged = staged.append_column("_ingested_at", ingested_at)
    # Final select drops dlt's own `_dlt_load_id`/`_dlt_id` — only the
    # contract's declared columns survive into Bronze.
    staged = cast_to_arrow_schema(staged, contract.ICEBERG_SCHEMA.as_arrow())

    validated = validate(
        staged.to_pandas(), contract.PANDERA_SCHEMA, table_identifier=contract.TABLE_IDENTIFIER
    )
    append(table, pa.Table.from_pandas(validated, preserve_index=False))

    return {
        "table": contract.TABLE_IDENTIFIER,
        "rows_committed": len(validated),
        "load_ids_committed": load_ids,
    }


def main() -> int:
    fs = extract_staging_filesystem()
    results = [build_bronze_table(fs, source_name, contract) for source_name, contract in SOURCES]
    print(json.dumps({"bronze_build_result": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
