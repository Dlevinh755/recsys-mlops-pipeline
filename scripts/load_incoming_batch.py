"""Nạp `incoming/batch_NN.parquet` vào source-db để mô phỏng "dữ liệu mới về"
(demo drift thật + Silver tăng dần). Xem `amazone_data/DATA.md` mục 7.

`incoming/batch_*.parquet` là test.parquet chia theo thời gian, cùng schema
với train.parquet. Dùng lại đúng các hàm ánh xạ của `seed_source_db.py` nên
dòng nạp vào có cùng dạng với dữ liệu seed (`event_type='rating'`).

    python scripts/load_incoming_batch.py --batch 0          # batch_00
    python scripts/load_incoming_batch.py --batch 0 1 2      # nhiều batch
    python scripts/load_incoming_batch.py --all
    python scripts/load_incoming_batch.py --all --dry-run    # chỉ in thống kê

Idempotent: user dùng ON CONFLICT DO NOTHING, interaction bỏ qua cặp
(user_id, product_id) đã có rating — chạy lại không nhân đôi dòng.
Dòng trỏ tới product không có trong source-db bị bỏ qua và được đếm.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg

from seed_source_db import (  # cùng thư mục scripts/
    DEFAULT_DATA_DIR,
    DEFAULT_DSN,
    build_interaction_rows,
    build_user_ids,
    load_interactions,
    load_users,
)


def main() -> int:
    # Console Windows mặc định cp1252 không in được tiếng Việt (UnicodeEncodeError).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--batch", type=int, nargs="+", help="số thứ tự batch (0 → batch_00)")
    group.add_argument("--all", action="store_true", help="nạp mọi batch trong incoming/")
    parser.add_argument("--dry-run", action="store_true", help="chỉ in thống kê, không ghi DB")
    args = parser.parse_args()

    incoming = args.data_dir / "incoming"
    if args.all:
        files = sorted(incoming.glob("batch_*.parquet"))
    else:
        files = [incoming / f"batch_{n:02d}.parquet" for n in args.batch]
    missing = [f for f in files if not f.exists()]
    if missing:
        raise SystemExit(f"Không thấy file: {', '.join(str(f) for f in missing)}")

    batches = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    train_items = set(pd.read_parquet(args.data_dir / "train.parquet")["parent_asin"])
    cold_share = float((~batches["parent_asin"].isin(train_items)).mean())
    print(
        f"{len(files)} batch, {len(batches)} dòng, rating trung bình {batches['rating'].mean():.3f}, "
        f"tỷ lệ item lạ so với train {cold_share:.3f}"
    )
    if args.dry_run:
        return 0

    dsn = os.getenv("SOURCE_DB_DSN", DEFAULT_DSN)
    timestamp = datetime.now(timezone.utc)
    with psycopg.connect(dsn) as conn:
        known_products = {row[0] for row in conn.execute("SELECT product_id FROM products").fetchall()}
        in_db = batches[batches["parent_asin"].isin(known_products)]
        unknown_product = len(batches) - len(in_db)

        load_users(conn, build_user_ids(in_db["user_id"]), timestamp)
        added, skipped = load_interactions(conn, build_interaction_rows(in_db), timestamp)
        total = conn.execute("SELECT count(*) FROM interactions").fetchone()[0]

    print(
        f"interactions +{added} ({skipped} đã có, {unknown_product} bỏ qua vì product không có trong source-db); "
        f"tổng interactions trong source-db: {total}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
