"""Phase 7 — upload `amazone_data/data_split/train.parquet` to MinIO once, so
`jobs/monitoring/data_drift_report.py` (runs containerized, no host access)
has a baseline to compare `silver.interactions` against.

Run on the host, same style as `scripts/seed_source_db.py`:

    python -m pip install pandas pyarrow s3fs
    python scripts/upload_monitoring_baseline.py

Idempotent: re-running overwrites the same object key, safe to run again
after regenerating `amazone_data/prepare_seed_data.py`'s output.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import s3fs

DEFAULT_DSN_HOST = "localhost"
# scripts/ -> recommendation-mlops/ -> MLOPS4REC/ -> amazone_data/data_split
DEFAULT_TRAIN_PARQUET = Path(__file__).resolve().parents[2] / "amazone_data" / "data_split" / "train.parquet"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--train-parquet", type=Path, default=DEFAULT_TRAIN_PARQUET)
    parser.add_argument("--endpoint", default=os.getenv("MONITORING_S3_ENDPOINT", "http://localhost:9000"))
    parser.add_argument("--access-key", default=os.getenv("MINIO_ROOT_USER", "minioadmin"))
    parser.add_argument("--secret-key", default=os.getenv("MINIO_ROOT_PASSWORD", "local_minio_password_change_me"))
    parser.add_argument("--bucket", default=os.getenv("MONITORING_BASELINE_BUCKET", "monitoring-baseline"))
    args = parser.parse_args()

    fs = s3fs.S3FileSystem(
        key=args.access_key,
        secret=args.secret_key,
        client_kwargs={"endpoint_url": args.endpoint},
        use_ssl=args.endpoint.startswith("https://"),
    )
    dest = f"{args.bucket}/train.parquet"
    fs.put(str(args.train_parquet), dest)
    size = fs.info(dest)["size"]
    print(f"Uploaded {args.train_parquet} -> s3://{dest} ({size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
