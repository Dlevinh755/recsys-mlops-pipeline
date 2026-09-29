"""`GET /users/{user_id}/history` — lịch sử tương tác của user, ghép đúng 2
nguồn mà ranker đang dùng (Feast: lịch sử batch; Redis: phiên hiện tại từ
`POST /interact`), nên panel UI phản ánh những gì model "nhìn thấy". Không
cache: phải tức thời sau `/interact`. Lỗi nguồn nào → coi như rỗng, không 5xx.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from serving.app.core.sequence_source import build_history, fetch_historical_events, fetch_recent_items
from serving.app.schemas.models import HistoryItem, HistoryResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/users/{user_id}/history", response_model=HistoryResponse)
def history(user_id: str, request: Request) -> HistoryResponse:
    state = request.app.state
    try:
        historical = fetch_historical_events(state.feature_store, user_id)
    except Exception:  # noqa: BLE001 - history is best-effort, never 5xx
        logger.exception("Feast history lookup failed for %s", user_id)
        historical = []
    try:
        recent = fetch_recent_items(state.redis_client, user_id)
    except Exception:  # noqa: BLE001
        logger.exception("Redis recent_items lookup failed for %s", user_id)
        recent = []

    items = [
        HistoryItem(**entry, **state.fallback.metadata(entry["product_id"]))
        for entry in build_history(historical, recent)
    ]
    return HistoryResponse(user_id=user_id, items=items)
