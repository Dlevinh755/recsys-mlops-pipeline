"""Danh sách user để UI cho chọn sẵn thay vì nhập tay `user_id`.

Nạp trực tiếp từ `gold.user_features` (cùng cách `fallback.py` nạp
`gold.item_features`: Iceberg là nguồn duy nhất, không gọi thẳng
source-db) — nên không phát sinh ngoại lệ kiến trúc mới. Sắp theo độ dài
chuỗi hành vi giảm dần: user có nhiều lịch sử nhất lên đầu, cho ra gợi ý
"có nghĩa" nhất khi demo.
"""

from __future__ import annotations

import asyncio
import logging

from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import gold_user_features

logger = logging.getLogger(__name__)


def rank_users_by_activity(rows: list[dict], top_n: int) -> list[dict]:
    """Pure step (unit-testable without Iceberg): most active first, ties
    broken by `user_id` so the order is deterministic."""
    ranked = sorted(rows, key=lambda row: (-(row.get("sequence_length") or 0), row["user_id"]))
    return [
        {"user_id": row["user_id"], "sequence_length": int(row.get("sequence_length") or 0)}
        for row in ranked[:top_n]
    ]


class UserCatalog:
    def __init__(self, refresh_interval_seconds: int, top_n: int) -> None:
        self._refresh_interval_seconds = refresh_interval_seconds
        self._top_n = top_n
        self._users: list[dict] = []
        self._task: asyncio.Task | None = None

    def users(self) -> list[dict]:
        return self._users

    def _load_once(self) -> None:
        table = get_catalog().load_table(gold_user_features.TABLE_IDENTIFIER)
        arrow = table.scan(selected_fields=("user_id", "sequence_length")).to_arrow()
        self._users = rank_users_by_activity(arrow.to_pylist(), self._top_n)

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval_seconds)
            try:
                await asyncio.to_thread(self._load_once)
            except Exception:  # noqa: BLE001 - refresh must never crash the loop
                logger.exception("User catalog refresh failed; keeping previous data")

    def start(self) -> None:
        try:
            self._load_once()
        except Exception:  # noqa: BLE001 - startup must not crash if Iceberg is briefly unreachable
            logger.exception("Initial user catalog load failed; will retry on next refresh")
        self._task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
