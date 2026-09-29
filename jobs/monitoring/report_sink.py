"""Phase 7 — write a monitoring report (HTML and/or JSON) to MinIO.

Same `s3fs.S3FileSystem` pattern as `jobs/materialize/export_to_parquet.py`
— reports are durable state (CLAUDE.md nguyên tắc #6: no important state on
a container's local filesystem), so they go to MinIO like every other
cross-job artifact, not to the monitoring-job container's own disk.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import s3fs

from reco_mlops_libs.common.env import require_env


def _filesystem() -> s3fs.S3FileSystem:
    endpoint = require_env("MONITORING_S3_ENDPOINT")
    return s3fs.S3FileSystem(
        key=require_env("MONITORING_S3_ACCESS_KEY"),
        secret=require_env("MONITORING_S3_SECRET_KEY"),
        client_kwargs={"endpoint_url": endpoint},
        use_ssl=endpoint.startswith("https://"),
    )


def save_report(job_name: str, *, html: str | None = None, data: dict | None = None) -> list[str]:
    """Write under `{bucket}/{job_name}/{utc timestamp}.{html,json}`. Returns
    the paths actually written (empty list if both `html` and `data` are
    `None`)."""
    bucket = require_env("MONITORING_REPORTS_BUCKET")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fs = _filesystem()
    written: list[str] = []

    if html is not None:
        path = f"{bucket}/{job_name}/{timestamp}.html"
        with fs.open(path, "w") as file:
            file.write(html)
        written.append(path)

    if data is not None:
        path = f"{bucket}/{job_name}/{timestamp}.json"
        with fs.open(path, "w") as file:
            json.dump(data, file, indent=2, default=str)
        written.append(path)

    return written
