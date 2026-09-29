"""Insert a batch of new upstream data into source-db to demonstrate the
Phase 1 watermark mechanism on non-trivial volume — see
`docs/modules/phase-1-extract-watermark.md`.

Every run adds NEW rows (never updates/deletes existing ones), so it is
safe to run repeatedly: product ids keep incrementing from whatever already
exists, and interactions reference the full existing user/product pool.

    python scripts/simulate_incremental_update.py                 # 20 products, 100 interactions
    python scripts/simulate_incremental_update.py --products 5 --interactions 30
"""

from __future__ import annotations

import argparse
import os
import random
from datetime import datetime, timedelta, timezone

import psycopg

DEFAULT_DSN = "postgresql://reco_app:local_source_password_change_me@localhost:5433/recommendation"

CATEGORIES = ("electronics", "books", "home", "sports", "beauty")
BRANDS = ("DemoTech", "DemoPress", "DemoHome", "DemoSport", "DemoGlow")
# Weighted so most interactions are views, purchases/ratings are rarer —
# gives build_gold.py's aggregates (num_views/num_purchases/avg_rating)
# something realistic to compute over.
EVENT_TYPES = ("view", "view", "view", "view", "cart", "cart", "purchase", "rating")


def _next_product_index(conn: psycopg.Connection) -> int:
    row = conn.execute(
        """
        SELECT product_id FROM products
        WHERE product_id ~ '^p[0-9]+$'
        ORDER BY (substring(product_id FROM 2))::int DESC
        LIMIT 1
        """
    ).fetchone()
    return int(row[0][1:]) + 1 if row else 1


def seed_products(conn: psycopg.Connection, count: int, timestamp: datetime) -> None:
    start = _next_product_index(conn)
    rows = [
        (
            f"p{index:03d}",
            f"Demo Product {index}",
            random.choice(CATEGORIES),
            random.choice(BRANDS),
            round(random.uniform(9.9, 199.9), 2),
            f"Auto-generated product {index}",
            timestamp,
            timestamp,
        )
        for index in range(start, start + count)
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO products (
                product_id, title, category, brand, price, description, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def seed_interactions(conn: psycopg.Connection, count: int, timestamp: datetime) -> None:
    user_ids = [row[0] for row in conn.execute("SELECT user_id FROM users").fetchall()]
    product_ids = [row[0] for row in conn.execute("SELECT product_id FROM products").fetchall()]

    rows = []
    for _ in range(count):
        event_type = random.choice(EVENT_TYPES)
        rating = round(random.uniform(1, 5), 1) if event_type == "rating" else None
        # Spread event_time over the past week so recency-based features
        # (Phase 3+) have something to differentiate on; created_at/updated_at
        # stay pinned to this run's timestamp — that's what the watermark
        # actually advances on.
        event_time = timestamp - timedelta(seconds=random.randint(0, 7 * 24 * 3600))
        rows.append((
            random.choice(user_ids),
            random.choice(product_ids),
            event_type,
            rating,
            event_time,
            timestamp,
            timestamp,
        ))

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO interactions (
                user_id, product_id, event_type, rating, event_time, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=int, default=20, help="new products to insert (default: 20)")
    parser.add_argument("--interactions", type=int, default=100, help="new interactions to insert (default: 100)")
    parser.add_argument("--seed", type=int, default=None, help="random seed, for reproducible batches")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    dsn = os.getenv("SOURCE_DB_DSN", DEFAULT_DSN)
    timestamp = datetime.now(timezone.utc)
    with psycopg.connect(dsn) as conn:
        seed_products(conn, args.products, timestamp)
        # Products must exist before interactions can reference them.
        seed_interactions(conn, args.interactions, timestamp)

    print(f"Inserted {args.products} products and {args.interactions} interactions "
          f"(updated_at={timestamp.isoformat()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
