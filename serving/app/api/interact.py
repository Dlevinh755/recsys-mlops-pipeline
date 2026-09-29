"""`POST /interact` — `docs/bao-cao-ky-thuat.md` mục 8.1. Two independent
writes: (A) Redis `recent_items:{user_id}` (immediate session signal, feeds
Hướng B's model input + `recency_boost.py`), (B) `source-db.interactions`
(durable, picked up by the next `extract-job` run via the existing dlt
watermark, ADR 0006). Also invalidates the user's cached homepage response
so the very next `/recommend/homepage` call reflects the change instead of
serving a stale cached list until the short TTL expires.
"""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, HTTPException, Request

from serving.app.core import cache, source_db_writer
from serving.app.core.sequence_source import RECENT_ITEMS_KEY_PREFIX
from serving.app.schemas.models import InteractRequest, InteractResponse

router = APIRouter()


@router.post("/interact", response_model=InteractResponse)
def interact(payload: InteractRequest, request: Request) -> InteractResponse:
    state = request.app.state

    with psycopg.connect(state.settings.source_db_dsn) as conn:
        try:
            source_db_writer.write_interaction(
                conn, payload.user_id, payload.item_id, payload.event_type, payload.rating
            )
        except source_db_writer.UnknownEntityError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    key = f"{RECENT_ITEMS_KEY_PREFIX}:{payload.user_id}"
    redis_client = state.redis_client
    redis_client.rpush(key, payload.item_id)
    redis_client.ltrim(key, -state.settings.recent_items_max_length, -1)
    redis_client.expire(key, state.settings.recent_items_ttl_seconds)

    cache.invalidate(redis_client, cache.homepage_key(payload.user_id))

    return InteractResponse(status="ok")
