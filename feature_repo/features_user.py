"""User feature view: recent-item sequence per user — the GRU sequence
model's primary input (ADR 0004). `ttl` is set very long because
`gold.user_features` is a fully-recomputed snapshot (one row per user,
timestamped with the batch run time, see `data_sources.py`) rather than a
stream of historical events — there is nothing for Feast to "expire" here.
"""

from datetime import timedelta

from feast import FeatureView, Field
from feast.types import Array, Int64, String, UnixTimestamp

from data_sources import user_features_source
from entities import user

user_recent_items = FeatureView(
    name="user_recent_items",
    entities=[user],
    ttl=timedelta(days=3650),
    schema=[
        Field(name="item_sequence", dtype=Array(String)),
        Field(name="event_time_sequence", dtype=Array(UnixTimestamp)),
        Field(name="sequence_length", dtype=Int64),
        Field(name="last_event_time", dtype=UnixTimestamp),
    ],
    online=True,
    source=user_features_source,
)
