"""Post-hoc re-rank per `docs/bao-cao-ky-thuat.md` mục 8.1: push items
similar to whatever the user just interacted with (`POST /interact`) to the
front of an already-scored list.

Complements, doesn't replace, Hướng B's model-input merge
(`sequence_source.py`): the model-input path makes GRU *implicitly* aware of
the new interaction (a diffuse score shift, not obviously visible), this
pass makes the effect *visible by construction* — which is what the
Definition of Done's demo scenario ("gọi `/interact` rồi gọi lại
`/recommend/homepage` -> item liên quan lên đầu") checks for.

`boost()` is pure (no I/O) so it's unit-testable without Redis.
`fetch_similar_items()` is the I/O side, reading what
`jobs/candidates/similar_items.py` materialized.
"""

from __future__ import annotations

import json

SIMILAR_ITEMS_KEY_PREFIX = "similar_items"


def fetch_similar_items(redis_client, item_id: str) -> list[str]:
    raw = redis_client.get(f"{SIMILAR_ITEMS_KEY_PREFIX}:{item_id}")
    return json.loads(raw) if raw else []


def boost(
    ranked_items: list[str], recent_items: list[str], similar_items_by_id: dict[str, list[str]]
) -> list[str]:
    """`ranked_items`: full scored candidate list (already sorted by model
    score), not yet truncated to top-N — an item must be a real scored
    candidate to be boosted, never introduced from outside the ranking.
    `recent_items`: chronological, oldest first (from
    `sequence_source.fetch_recent_items`). `similar_items_by_id`: pre-fetched
    `{item_id: [similar_item_id, ...]}` for every id in `recent_items`.
    """
    boosted: list[str] = []
    seen: set[str] = set()
    ranked_set = set(ranked_items)
    for recent_item in reversed(recent_items):  # most recent interaction wins the front
        for similar_item in similar_items_by_id.get(recent_item, []):
            if similar_item in ranked_set and similar_item not in seen:
                boosted.append(similar_item)
                seen.add(similar_item)
    remainder = [item for item in ranked_items if item not in seen]
    return boosted + remainder
