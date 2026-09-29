"""Schema contract for `gold.item_features` — one row per `product_id`,
aggregated from `silver.products` + `silver.interactions`
(see `sql/gold/gold_item_features.sql`). Full recompute every run (MVP
scale — see `docs/modules/phase-2-lakehouse.md` for the tradeoff).
"""

from __future__ import annotations

from pandera.pandas import Check, Column, DataFrameSchema
from pyiceberg.schema import Schema
from pyiceberg.types import (
    DecimalType,
    DoubleType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

TABLE_IDENTIFIER = "gold.item_features"

ICEBERG_SCHEMA = Schema(
    NestedField(1, "product_id", StringType(), required=True),
    NestedField(15, "title", StringType(), required=True),
    NestedField(2, "category", StringType(), required=True),
    NestedField(3, "brand", StringType(), required=False),
    NestedField(4, "price", DecimalType(12, 2), required=False),
    NestedField(5, "num_interactions", LongType(), required=True),
    NestedField(6, "num_views", LongType(), required=True),
    NestedField(7, "num_cart_adds", LongType(), required=True),
    NestedField(8, "num_purchases", LongType(), required=True),
    NestedField(9, "num_ratings", LongType(), required=True),
    NestedField(10, "avg_rating", DoubleType(), required=False),
    NestedField(11, "distinct_users", LongType(), required=True),
    NestedField(12, "last_interaction_at", TimestamptzType(), required=False),
    NestedField(13, "computed_at", TimestamptzType(), required=True),
    NestedField(14, "image_url", StringType(), required=False),
)

PANDERA_SCHEMA = DataFrameSchema(
    columns={
        "product_id": Column(str, nullable=False, unique=True),
        "title": Column(str, nullable=False),
        "category": Column(str, nullable=False),
        "brand": Column(str, nullable=True),
        "price": Column(float, checks=Check.ge(0), nullable=True),
        "num_interactions": Column(int, checks=Check.ge(0), nullable=False),
        "num_views": Column(int, checks=Check.ge(0), nullable=False),
        "num_cart_adds": Column(int, checks=Check.ge(0), nullable=False),
        "num_purchases": Column(int, checks=Check.ge(0), nullable=False),
        "num_ratings": Column(int, checks=Check.ge(0), nullable=False),
        "avg_rating": Column(float, checks=Check.in_range(1, 5), nullable=True),
        "distinct_users": Column(int, checks=Check.ge(0), nullable=False),
        "last_interaction_at": Column("datetime64[us, UTC]", nullable=True),
        "computed_at": Column("datetime64[us, UTC]", nullable=False),
        "image_url": Column(str, nullable=True),
    },
    strict=True,
    coerce=True,
)
