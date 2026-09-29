"""`GET /recommend/similar` — reads what `jobs/candidates/similar_items.py`
already materialized into Redis; never computes similarity at request time.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from serving.app.core import cache, recency_boost
from serving.app.schemas.models import SimilarResponse

router = APIRouter()


@router.get("/recommend/similar", response_model=SimilarResponse)
def similar(item_id: str, request: Request) -> SimilarResponse:
    state = request.app.state
    cache_key = cache.similar_key(item_id)
    cached = cache.get_cached(state.redis_client, cache_key)
    if cached is not None:
        return SimilarResponse(**cached)

    similar_ids = recency_boost.fetch_similar_items(state.redis_client, item_id)
    if similar_ids:
        response = SimilarResponse(item_id=item_id, items=similar_ids, source="similar_items")
    else:
        response = SimilarResponse(item_id=item_id, items=state.fallback.popular_items(), source="fallback")

    cache.set_cached(
        state.redis_client, cache_key, response.model_dump(), state.settings.similar_cache_ttl_seconds
    )
    return response
