"""Load the real Amazon seed dataset into source-db (`products`/`users`/`interactions`).

Reads `train.parquet` (interactions) + `meta.parquet` (products) from
`amazone_data/data_split/` — produced by `amazone_data/prepare_seed_data.py`,
schema/stats documented in `amazone_data/DATA.md`. This complements (does not
replace) the tiny fixture in `infra/source-db/init/seed_data.sql`, which stays
as the cheap always-on baseline; this script adds the full ~16k-interaction
dataset needed for Phase 3/4 (feature store, GRU4Rec training) to have enough
users/items to be statistically meaningful — see `docs/STATUS.md` Phase 4.

Every row is inserted as `event_type='rating'` (the source data is Amazon
review data, i.e. explicit rating feedback, not clickstream).

Two modes:

1. Default — connect to a running source-db and load directly. Idempotent:
   products/users use `ON CONFLICT DO NOTHING`; interactions are deduplicated
   against `(user_id, product_id)` pairs already present with
   `event_type='rating'` (safe per `amazone_data/DATA.md` 4.6: that pair is
   unique in the source data) — safe to run more than once.

       python -m pip install -r requirements/extract.txt pandas pyarrow
       python scripts/seed_source_db.py

2. `--dump-sql` — generate a static `.sql` file (plain multi-row INSERTs, no
   DB connection needed) meant to sit in `infra/source-db/init/` next to
   `seed_data.sql`. Postgres auto-runs every `.sql` file there the first time
   the `source_db_data` volume is created (see the official postgres image's
   docker-entrypoint-initdb.d convention) — so a fresh `make up` (after
   `make clean`, or on a new machine) already has the full Amazon dataset,
   with no manual seeding step. Regenerate this file whenever the mapping
   logic below or the source parquet changes; it is a frozen snapshot, not
   re-derived at container start.

       python scripts/seed_source_db.py --dump-sql infra/source-db/init/seed_data_amazon.sql
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import pandas as pd
import psycopg

DEFAULT_DSN = "postgresql://reco_app:local_source_password_change_me@localhost:5433/recommendation"
# scripts/ -> recommendation-mlops/ -> MLOPS4REC/ -> amazone_data/data_split
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "amazone_data" / "data_split"

ProductRow = tuple[str, str, str, str | None, float, None, str]
InteractionRow = tuple[str, str, str, float, datetime]


def _clean_category(main_category: object, categories: object) -> str:
    if isinstance(main_category, str) and main_category.strip():
        return main_category
    if categories is not None and len(categories) > 0:
        return str(categories[0])
    return "unknown"


def build_product_rows(meta: pd.DataFrame) -> list[ProductRow]:
    return [
        (
            str(r.parent_asin),
            str(r.title),
            _clean_category(r.main_category, r.categories),
            None if pd.isna(r.store) else str(r.store),
            round(float(r.price), 2),
            None,  # description — not present in meta.parquet
            str(r.image_url),  # always present — clean_meta() drops items without an image
        )
        for r in meta.itertuples(index=False)
    ]


def build_user_ids(user_ids: pd.Series) -> list[str]:
    return sorted(str(uid) for uid in user_ids.unique())


def build_interaction_rows(train: pd.DataFrame) -> list[InteractionRow]:
    rows = []
    for r in train.sort_values("timestamp").itertuples(index=False):
        event_time = pd.Timestamp(r.timestamp, unit="ms", tz="UTC").to_pydatetime()
        rows.append((str(r.user_id), str(r.parent_asin), "rating", float(r.rating), event_time))
    return rows


# --- Mode 1: load directly into a running source-db ------------------------


def load_products(conn: psycopg.Connection, rows: Sequence[ProductRow], timestamp: datetime) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO products (
                product_id, title, category, brand, price, description, image_url, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (product_id) DO NOTHING
            """,
            [row + (timestamp, timestamp) for row in rows],
        )


def load_users(conn: psycopg.Connection, user_ids: Sequence[str], timestamp: datetime) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO users (user_id, display_name, created_at, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            [(uid, None, timestamp, timestamp) for uid in user_ids],
        )


def load_interactions(
    conn: psycopg.Connection, rows: Sequence[InteractionRow], timestamp: datetime
) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, product_id FROM interactions WHERE event_type = 'rating'")
        existing = set(cur.fetchall())

    new_rows = [row + (timestamp, timestamp) for row in rows if (row[0], row[1]) not in existing]
    if new_rows:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO interactions (
                    user_id, product_id, event_type, rating, event_time, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                new_rows,
            )
    return len(new_rows), len(rows) - len(new_rows)


def seed_live_db(dsn: str, meta: pd.DataFrame, train: pd.DataFrame) -> None:
    timestamp = datetime.now(timezone.utc)
    product_rows = build_product_rows(meta)
    user_ids = build_user_ids(train["user_id"])
    interaction_rows = build_interaction_rows(train)

    with psycopg.connect(dsn) as conn:
        def count(table: str) -> int:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

        before = {t: count(t) for t in ("products", "users", "interactions")}

        load_products(conn, product_rows, timestamp)
        # Products/users must exist before interactions can reference them (FK).
        load_users(conn, user_ids, timestamp)
        n_new_interactions, n_skipped = load_interactions(conn, interaction_rows, timestamp)

        after = {t: count(t) for t in ("products", "users", "interactions")}

    print(
        f"products +{after['products'] - before['products']} (dataset has {len(product_rows)}), "
        f"users +{after['users'] - before['users']} (dataset has {len(user_ids)}), "
        f"interactions +{n_new_interactions} ({n_skipped} already present, skipped)"
    )


# --- Mode 2: generate a static .sql file for docker-entrypoint-initdb.d ----


def _sql_str(value: object) -> str:
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


def _sql_ts(dt: datetime) -> str:
    return "'" + dt.isoformat() + "'"


def _chunks(items: Sequence, size: int) -> Iterator[Sequence]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _insert_statements(
    table: str, columns: Sequence[str], value_lines: Iterable[str], *, batch_size: int = 1000
) -> Iterator[str]:
    lines = list(value_lines)
    for batch in _chunks(lines, batch_size):
        cols = ", ".join(columns)
        values = ",\n    ".join(batch)
        yield f"INSERT INTO {table} ({cols}) VALUES\n    {values};\n"


def generate_sql_dump(path: Path, meta: pd.DataFrame, train: pd.DataFrame) -> None:
    timestamp = datetime.now(timezone.utc)
    ts_literal = _sql_ts(timestamp)

    product_rows = build_product_rows(meta)
    user_ids = build_user_ids(train["user_id"])
    interaction_rows = build_interaction_rows(train)

    user_lines = (f"({_sql_str(uid)}, NULL, {ts_literal}, {ts_literal})" for uid in user_ids)
    product_lines = (
        f"({_sql_str(pid)}, {_sql_str(title)}, {_sql_str(category)}, {_sql_str(brand)}, "
        f"{price:.2f}, {_sql_str(description)}, {_sql_str(image_url)}, {ts_literal}, {ts_literal})"
        for pid, title, category, brand, price, description, image_url in product_rows
    )
    interaction_lines = (
        f"({_sql_str(uid)}, {_sql_str(pid)}, {_sql_str(event_type)}, {rating:.1f}, "
        f"{_sql_ts(event_time)}, {ts_literal}, {ts_literal})"
        for uid, pid, event_type, rating, event_time in interaction_rows
    )

    header = f"""-- Auto-generated by scripts/seed_source_db.py --dump-sql — DO NOT EDIT BY HAND.
-- Snapshot of amazone_data/data_split/{{train,meta}}.parquet as of {timestamp.isoformat()}.
-- Regenerate after changing the mapping logic or the source dataset:
--   python scripts/seed_source_db.py --dump-sql {path.as_posix()}
--
-- Loaded automatically by the official postgres image on first container
-- start against an empty `source_db_data` volume (docker-entrypoint-initdb.d),
-- alongside seed_data.sql — see docs/modules/phase-1-extract-watermark.md.

BEGIN;

"""
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(header)
        f.write(f"-- users ({len(user_ids)} rows)\n")
        for stmt in _insert_statements("users", ["user_id", "display_name", "created_at", "updated_at"], user_lines):
            f.write(stmt)
        f.write(f"\n-- products ({len(product_rows)} rows)\n")
        for stmt in _insert_statements(
            "products",
            [
                "product_id", "title", "category", "brand", "price", "description",
                "image_url", "created_at", "updated_at",
            ],
            product_lines,
        ):
            f.write(stmt)
        f.write(f"\n-- interactions ({len(interaction_rows)} rows)\n")
        for stmt in _insert_statements(
            "interactions",
            ["user_id", "product_id", "event_type", "rating", "event_time", "created_at", "updated_at"],
            interaction_lines,
        ):
            f.write(stmt)
        f.write("\nCOMMIT;\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"thư mục chứa train.parquet/meta.parquet (mặc định: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--dump-sql",
        type=Path,
        default=None,
        help="thay vì kết nối DB, sinh file .sql tĩnh tại đường dẫn này (dùng cho docker-entrypoint-initdb.d)",
    )
    args = parser.parse_args()

    train = pd.read_parquet(args.data_dir / "train.parquet")
    meta = pd.read_parquet(args.data_dir / "meta.parquet")

    if args.dump_sql is not None:
        generate_sql_dump(args.dump_sql, meta, train)
        print(f"Wrote SQL dump to {args.dump_sql} ({args.dump_sql.stat().st_size / 1e6:.2f} MB)")
        return 0

    dsn = os.getenv("SOURCE_DB_DSN", DEFAULT_DSN)
    seed_live_db(dsn, meta, train)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
