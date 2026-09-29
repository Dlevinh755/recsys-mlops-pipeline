"""Schema contract for `silver.interactions` — one row per
`interaction_id` (deduplicated from `bronze.interactions`, see
`sql/silver/silver_interactions.sql`).
"""

from __future__ import annotations

from pandera.pandas import Check, Column, DataFrameSchema
from pyiceberg.schema import Schema
from pyiceberg.types import (
    DecimalType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from reco_mlops_libs.iceberg.schema_contracts.bronze_interactions import EVENT_TYPES

TABLE_IDENTIFIER = "silver.interactions"

ICEBERG_SCHEMA = Schema(
    NestedField(1, "interaction_id", LongType(), required=True),
    NestedField(2, "user_id", StringType(), required=True),
    NestedField(3, "product_id", StringType(), required=True),
    NestedField(4, "event_type", StringType(), required=True),
    NestedField(5, "rating", DecimalType(2, 1), required=False),
    NestedField(6, "event_time", TimestamptzType(), required=True),
    NestedField(7, "created_at", TimestamptzType(), required=True),
    NestedField(8, "updated_at", TimestamptzType(), required=True),
)

PANDERA_SCHEMA = DataFrameSchema(
    columns={
        "interaction_id": Column(int, nullable=False, unique=True),
        "user_id": Column(str, nullable=False),
        "product_id": Column(str, nullable=False),
        "event_type": Column(str, checks=Check.isin(EVENT_TYPES), nullable=False),
        "rating": Column(float, checks=Check.in_range(1, 5), nullable=True),
        "event_time": Column("datetime64[us, UTC]", nullable=False),
        "created_at": Column("datetime64[us, UTC]", nullable=False),
        "updated_at": Column("datetime64[us, UTC]", nullable=False),
    },
    strict=True,
    coerce=True,
)
