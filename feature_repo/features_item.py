"""Item feature view — reuses `gold.item_features` as-is from Phase 2 (no
new aggregation logic for Phase 3, per the Phase 3 kickoff decision: GRU
learns item embeddings directly from `product_id`, these are only for
candidate filtering / business rules downstream).
"""

from datetime import timedelta

from feast import FeatureView, Field
from feast.types import Float64, Int64, String, UnixTimestamp

from data_sources import item_features_source
from entities import product

item_features = FeatureView(
    name="item_features",
    entities=[product],
    ttl=timedelta(days=3650),
    schema=[
        Field(name="category", dtype=String),
        Field(name="brand", dtype=String),
        Field(name="price", dtype=Float64),
        Field(name="num_interactions", dtype=Int64),
        Field(name="num_views", dtype=Int64),
        Field(name="num_cart_adds", dtype=Int64),
        Field(name="num_purchases", dtype=Int64),
        Field(name="num_ratings", dtype=Int64),
        Field(name="avg_rating", dtype=Float64),
        Field(name="distinct_users", dtype=Int64),
        Field(name="last_interaction_at", dtype=UnixTimestamp),
    ],
    online=True,
    source=item_features_source,
)
