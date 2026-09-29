"""`GET /recommend/homepage` — Definition of Done (Phase 5) requires 3
branches to all return 200: cache hit, cache miss with a real model, and
model/feature unavailable (fallback), never a 5xx.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from serving.app.core import cache, recency_boost
from serving.app.core.sequence_source import fetch_recent_items
from serving.app.schemas.models import HomepageResponse, RecommendedItem

router = APIRouter()


def _item(state, product_id: str, score: float) -> RecommendedItem:
    return RecommendedItem(product_id=product_id, score=score, **state.fallback.metadata(product_id))


@router.get("/recommend/homepage", response_model=HomepageResponse)
def homepage(user_id: str, request: Request) -> HomepageResponse:
    state = request.app.state
    cache_key = cache.homepage_key(user_id)
    cached = cache.get_cached(state.redis_client, cache_key)
    if cached is not None:
        return HomepageResponse(**cached)

    candidates = state.fallback.active_items()
    ranker = state.model_loader.get_ranker(state.redis_client, state.feature_store)

    if ranker is None or not candidates:
        response = HomepageResponse(
            user_id=user_id,
            items=[_item(state, product_id, 0.0) for product_id in state.fallback.popular_items()],
            source="fallback",
        )
    else:
        scores = ranker.predict(user_id, candidates)
        ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
        ranked_ids = [product_id for product_id, _ in ranked]
        score_by_id = dict(ranked)

        if all(score == 0.0 for _, score in ranked):
            # `ranker.predict()` returns all-neutral scores for a true
            # cold-start user (no sequence anywhere) — fall back instead of
            # presenting an arbitrarily-ordered list as if it were ranked.
            response = HomepageResponse(
                user_id=user_id,
                items=[_item(state, product_id, 0.0) for product_id in state.fallback.popular_items()],
                source="fallback",
            )
        else:
            recent_items = fetch_recent_items(state.redis_client, user_id)
            similar_by_id = {
                item: recency_boost.fetch_similar_items(state.redis_client, item)
                for item in recent_items
            }
            boosted_ids = recency_boost.boost(ranked_ids, recent_items, similar_by_id)
            top_ids = boosted_ids[: state.settings.homepage_top_n]
            response = HomepageResponse(
                user_id=user_id,
                items=[_item(state, product_id, score_by_id.get(product_id, 0.0)) for product_id in top_ids],
                source="model",
            )

    cache.set_cached(state.redis_client, cache_key, response.model_dump(), state.settings.cache_ttl_seconds)
    return response
