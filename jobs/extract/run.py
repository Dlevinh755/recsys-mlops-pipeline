from __future__ import annotations

import json
import os
from pathlib import Path

import dlt
from dlt.destinations import filesystem

from jobs.extract.dlt_writer import make_source_resource
from jobs.extract.source_spec import SourceSpec
from jobs.extract.sources.interactions import SOURCE as INTERACTIONS
from jobs.extract.sources.products import SOURCE as PRODUCTS


def parse_fail_after_rows(value: str) -> tuple[str, int] | None:
    """Parse `EXTRACT_FAIL_AFTER_ROWS=<source>:<n>` (empty string -> None)."""
    value = value.strip()
    if not value:
        return None
    source_name, _, count = value.partition(":")
    if not source_name or not count:
        raise ValueError(f"EXTRACT_FAIL_AFTER_ROWS must be '<source>:<n>', got {value!r}")
    return source_name.strip(), int(count)


def extract_source(
    source: SourceSpec,
    dsn: str,
    bucket_url: str,
    s3_credentials: dict[str, str],
    pipelines_dir: Path,
    page_size: int,
    fail_after_rows: tuple[str, int] | None,
) -> int:
    pipeline = dlt.pipeline(
        pipeline_name=f"reco_extract_{source.name}",
        pipelines_dir=str(pipelines_dir),
        destination=filesystem(bucket_url=bucket_url, credentials=s3_credentials),
        dataset_name="staging",
        restore_from_destination=True,
    )
    resource, counter = make_source_resource(source, dsn, page_size, fail_after_rows)
    try:
        load_info = pipeline.run(resource, loader_file_format="parquet")
    except Exception as error:
        print(json.dumps({
            "source": source.name,
            "status": "failed",
            "error": f"{type(error).__name__}: {error}",
            "rows_attempted": counter["rows"],
        }))
        raise
    print(json.dumps({
        "source": source.name,
        "status": "succeeded",
        "rows": counter["rows"],
        "load_ids": list(load_info.loads_ids),
    }))
    return counter["rows"]


def main() -> int:
    dsn = os.environ["SOURCE_DB_DSN"]
    bucket_url = f"s3://{os.environ['EXTRACT_S3_BUCKET']}"
    s3_credentials = {
        "aws_access_key_id": os.environ["EXTRACT_S3_ACCESS_KEY"],
        "aws_secret_access_key": os.environ["EXTRACT_S3_SECRET_KEY"],
        "endpoint_url": os.environ["EXTRACT_S3_ENDPOINT"],
        "region_name": os.getenv("EXTRACT_S3_REGION", "us-east-1"),
    }
    pipelines_dir = Path(os.getenv("DLT_PIPELINES_DIR", "/data/dlt-pipelines"))
    page_size = int(os.getenv("EXTRACT_PAGE_SIZE", "1000"))
    if page_size <= 0:
        raise ValueError("EXTRACT_PAGE_SIZE must be positive")
    fail_after_rows = parse_fail_after_rows(os.getenv("EXTRACT_FAIL_AFTER_ROWS", ""))

    total = 0
    results = []
    for source in (PRODUCTS, INTERACTIONS):
        rows_loaded = extract_source(
            source, dsn, bucket_url, s3_credentials, pipelines_dir, page_size, fail_after_rows
        )
        total += rows_loaded
        results.append({"source": source.name, "rows": rows_loaded})
    print(json.dumps({"status": "succeeded", "sources": results, "total_extracted_rows": total}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
