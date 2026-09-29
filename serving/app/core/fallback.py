"""Popularity fallback + active-item candidate pool — used whenever the
model isn't loaded yet or a user has no usable sequence at all (true
cold-start), and as the candidate pool `homepage.py` scores on the happy
path.

Loaded once at startup and refreshed periodically directly from
`gold.item_features` — cheap at this catalog size (~1.5k products), so no
separate `jobs/candidates/popular_items.py` batch job/Redis materialization
is needed (see `docs/modules/phase-5-serving.md` for this scope decision).
"""

from __future__ import annotations

import asyncio
import logging

from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import gold_item_features

logger = logging.getLogger(__name__)


def rank_items_by_popularity(rows: list[dict]) -> list[str]:
    """Pure sort step, factored out for unit testing without Iceberg — each
    row is expected to have `product_id`, `num_purchases`, `num_views`
    (missing/`None` values treated as 0)."""
    ranked = sorted(
        rows, key=lambda row: (row.get("num_purchases") or 0, row.get("num_views") or 0), reverse=True
    )
    return [row["product_id"] for row in ranked]


def build_item_metadata(row: dict) -> dict[str, str | None]:
    """`title`/`image_url` straight from the row; `description` is
    synthesized from `category` + `price` because the real `description`
    column (from `products`) is always NULL for this dataset — Amazon seed
    data has no real product description text (see `amazone_data/DATA.md`).
    Pure function, factored out for unit testing without Iceberg."""
    category = row.get("category")
    price = row.get("price")
    if category and price is not None:
        description = f"{category} · ${float(price):.2f}"
    else:
        description = category or None
    return {
        "title": row.get("title"),
        "description": description,
        "image_url": row.get("image_url"),
    }


class FallbackCatalog:
    def __init__(self, refresh_interval_seconds: int, top_n: int) -> None:
        self._refresh_interval_seconds = refresh_interval_seconds
        self._top_n = top_n
        self._active_items: list[str] = []
        self._metadata_by_id: dict[str, dict[str, str | None]] = {}
        self._task: asyncio.Task | None = None

    def active_items(self) -> list[str]:
        """Full catalog, most popular first — used both as the candidate
        pool for `ranker.predict()` and (truncated) as the fallback list."""
        return self._active_items

    def popular_items(self) -> list[str]:
        return self._active_items[: self._top_n]

    def metadata(self, product_id: str) -> dict[str, str | None]:
        """`title`/`description`/`image_url`, đã nạp cùng lúc với
        `_load_once()` — không query Iceberg riêng cho từng request."""
        return self._metadata_by_id.get(product_id, {"title": None, "description": None, "image_url": None})

    def _load_once(self) -> None:
        catalog = get_catalog()
        table = catalog.load_table(gold_item_features.TABLE_IDENTIFIER)
        arrow = table.scan(
            selected_fields=(
                "product_id", "num_purchases", "num_views", "title", "category", "price", "image_url",
            )
        ).to_arrow()
        rows = arrow.to_pylist()
        self._active_items = rank_items_by_popularity(rows)
        self._metadata_by_id = {row["product_id"]: build_item_metadata(row) for row in rows}

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval_seconds)
            try:
                await asyncio.to_thread(self._load_once)
            except Exception:  # noqa: BLE001 - refresh must never crash the loop
                logger.exception("Fallback catalog refresh failed; keeping previous data")

    def start(self) -> None:
        try:
            self._load_once()
        except Exception:  # noqa: BLE001 - startup must not crash if Iceberg is briefly unreachable
            logger.exception("Initial fallback catalog load failed; will retry on next refresh")
        self._task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
