"""Redis response cache for `/recommend/homepage` and `/recommend/similar`.

Homepage's TTL is short (`Settings.cache_ttl_seconds`, default 60s) because
it must reflect `POST /interact` promptly — `api/interact.py` also
explicitly deletes a user's homepage cache key right after writing, so a
stale cached list is never served immediately after an interaction (waiting
out the TTL would fail the Definition of Done, not just be slow).
"""

from __future__ import annotations

import json
from typing import Any

from prometheus_client import Counter

HOMEPAGE_PREFIX = "cache:homepage"
SIMILAR_PREFIX = "cache:similar"

# Phase 7 — `bao-cao-ky-thuat.md` mục 9.1 names "tỷ lệ cache hit" as an
# operational metric from the start, but Phase 5 never wired a counter for
# it (only request count/latency). Filling that in here, not a new metric
# introduced out of scope.
CACHE_LOOKUPS = Counter("cache_lookups_total", "Cache lookups by result", ["result"])


def homepage_key(user_id: str) -> str:
    return f"{HOMEPAGE_PREFIX}:{user_id}"


def similar_key(item_id: str) -> str:
    return f"{SIMILAR_PREFIX}:{item_id}"


def get_cached(redis_client, key: str) -> dict[str, Any] | None:
    raw = redis_client.get(key)
    CACHE_LOOKUPS.labels("hit" if raw else "miss").inc()
    return json.loads(raw) if raw else None


def set_cached(redis_client, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
    redis_client.set(key, json.dumps(value), ex=ttl_seconds)


def invalidate(redis_client, key: str) -> None:
    redis_client.delete(key)
