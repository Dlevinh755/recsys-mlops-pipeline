"""ADR 0014 — the drift baseline on MinIO (`MONITORING_BASELINE_BUCKET`).

Two generations, same two columns (`parent_asin`, `rating`):

* `train.parquet` — the fixed 80% seed period (`scripts/upload_monitoring_baseline.py`),
  the baseline since Phase 7 and the fallback when nothing else exists.
* `current.parquet` + `current.meta.json` — written by `refresh_baseline.py`
  when a new model version becomes `production`: the Silver interactions that
  version trained on, so "drift" always means "drift since the model in
  production last learned". Without it the baseline never moves and, once a
  retrain has absorbed the new data, drift would still read `true` forever.

`prefix` exists for tests (`it_test_*` keys next to the real ones, MinIO real).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import s3fs

from reco_mlops_libs.common.env import require_env

TRAIN_KEY = "train.parquet"
CURRENT_KEY = "current.parquet"
META_KEY = "current.meta.json"
COLUMNS = ["parent_asin", "rating"]


def baseline_filesystem() -> s3fs.S3FileSystem:
    endpoint = require_env("MONITORING_S3_ENDPOINT")
    return s3fs.S3FileSystem(
        key=require_env("MONITORING_S3_ACCESS_KEY"),
        secret=require_env("MONITORING_S3_SECRET_KEY"),
        client_kwargs={"endpoint_url": endpoint},
        use_ssl=endpoint.startswith("https://"),
    )


def _path(bucket: str, key: str, prefix: str) -> str:
    return f"{bucket}/{prefix}{key}"


def read_meta(fs, bucket: str, prefix: str = "") -> dict | None:
    path = _path(bucket, META_KEY, prefix)
    if not fs.exists(path):
        return None
    with fs.open(path, "r") as file:
        return json.load(file)


def load_baseline(fs, bucket: str, prefix: str = "") -> tuple[pd.DataFrame, dict]:
    """`(frame, info)`. `info["source"]` is `"current"` (model-tracking baseline,
    plus its meta) or `"train.parquet"` (fallback, no meta)."""
    meta = read_meta(fs, bucket, prefix)
    current_path = _path(bucket, CURRENT_KEY, prefix)
    if meta is not None and fs.exists(current_path):
        with fs.open(current_path, "rb") as file:
            return pd.read_parquet(file, columns=COLUMNS), {"source": "current", **meta}
    with fs.open(_path(bucket, TRAIN_KEY, prefix), "rb") as file:
        frame = pd.read_parquet(file, columns=COLUMNS)
    return frame, {"source": TRAIN_KEY, "rows": len(frame)}


def write_baseline(fs, bucket: str, frame: pd.DataFrame, meta: dict, prefix: str = "") -> dict:
    """Parquet first, meta last: a reader that sees the meta always finds the
    parquet it describes (the meta is what marks the baseline as current)."""
    frame = frame[COLUMNS]
    with fs.open(_path(bucket, CURRENT_KEY, prefix), "wb") as file:
        frame.to_parquet(file, index=False)
    meta = {**meta, "rows": len(frame), "created_at": datetime.now(timezone.utc).isoformat()}
    with fs.open(_path(bucket, META_KEY, prefix), "w") as file:
        json.dump(meta, file, indent=2)
    return meta
