"""Builds the item sequence GRU4Rec scores against at request time —
implements Hướng B (see `docs/modules/phase-5-serving.md`, resolving the
question ADR 0004 left open): the sequence fed to the model is the
Feast-materialized history (`gold.user_features`, may lag by up to one
`feast materialize` cycle) with whatever was just written by
`POST /interact` (Redis `recent_items:{user_id}`, immediate) appended on
top, then trimmed to the trained sequence length. Neither source alone is
enough — history alone misses what just happened; the session signal alone
would throw away everything the model actually learned from.

`merge_sequence`/`to_indices` are pure (no I/O) so they're unit-testable
without Feast/Redis. `fetch_historical_sequence`/`fetch_recent_items` are
the I/O side, used by `core/model_loader.py::ServingRanker`.
"""

from __future__ import annotations

# Must match libs/src/reco_mlops_libs/iceberg/schema_contracts/gold_user_features.py
MAX_SEQUENCE_LENGTH = 10

RECENT_ITEMS_KEY_PREFIX = "recent_items"


def merge_sequence(historical_items: list[str], recent_items: list[str]) -> list[str]:
    """Both inputs chronological, oldest first. Concatenate then keep only
    the most recent `MAX_SEQUENCE_LENGTH` — recent, session-fresh items
    always win the available slots since they're strictly newer."""
    merged = [*historical_items, *recent_items]
    return merged[-MAX_SEQUENCE_LENGTH:]


def to_indices(items: list[str], vocab: dict[str, int]) -> list[int]:
    return [vocab[item] for item in items if item in vocab]


def fetch_historical_sequence(feature_store, user_id: str) -> list[str]:
    result = feature_store.get_online_features(
        features=["user_recent_items:item_sequence"],
        entity_rows=[{"user_id": user_id}],
    ).to_dict()
    sequences = result.get("item_sequence") or [[]]
    return sequences[0] or []


def fetch_recent_items(redis_client, user_id: str) -> list[str]:
    return list(redis_client.lrange(f"{RECENT_ITEMS_KEY_PREFIX}:{user_id}", 0, -1))


def _to_iso(value) -> str | None:
    """Feast returns `UnixTimestamp` array elements as `datetime` (or, on
    some paths, epoch seconds) — normalize to an ISO string for the API."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        from datetime import datetime, timezone

        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def fetch_historical_events(feature_store, user_id: str) -> list[tuple[str, str | None]]:
    """`(product_id, iso_time)` pairs, oldest first. Separate from
    `fetch_historical_sequence` on purpose: the ranker path must not change."""
    result = feature_store.get_online_features(
        features=["user_recent_items:item_sequence", "user_recent_items:event_time_sequence"],
        entity_rows=[{"user_id": user_id}],
    ).to_dict()
    items = (result.get("item_sequence") or [[]])[0] or []
    times = (result.get("event_time_sequence") or [[]])[0] or []
    return [(item, _to_iso(times[i]) if i < len(times) else None) for i, item in enumerate(items)]


def build_history(historical_events: list[tuple[str, str | None]], recent_items: list[str]) -> list[dict]:
    """Newest first. Session items (Redis, from `POST /interact`) are newer
    than every historical item but carry no timestamp — labelled `session`."""
    session = [{"product_id": item, "event_time": None, "source": "session"} for item in recent_items]
    history = [
        {"product_id": item, "event_time": event_time, "source": "history"}
        for item, event_time in historical_events
    ]
    return [*reversed(session), *reversed(history)]
