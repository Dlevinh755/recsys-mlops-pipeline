"""Schema contract for `bronze.interactions`. See `bronze_products.py` for
why the lineage columns (`_extraction_run_id`, `_ingested_at`) exist.
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema
from pyiceberg.schema import Schema
from pyiceberg.types import (
    DecimalType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

TABLE_IDENTIFIER = "bronze.interactions"

EVENT_TYPES = {"view", "cart", "purchase", "rating"}

ICEBERG_SCHEMA = Schema(
    NestedField(1, "interaction_id", LongType(), required=True),
    NestedField(2, "user_id", StringType(), required=True),
    NestedField(3, "product_id", StringType(), required=True),
    NestedField(4, "event_type", StringType(), required=True),
    NestedField(5, "rating", DecimalType(2, 1), required=False),
    NestedField(6, "event_time", TimestamptzType(), required=True),
    NestedField(7, "created_at", TimestamptzType(), required=True),
    NestedField(8, "updated_at", TimestamptzType(), required=True),
    NestedField(9, "_extraction_run_id", StringType(), required=True),
    NestedField(10, "_ingested_at", TimestamptzType(), required=True),
)

PANDERA_SCHEMA = DataFrameSchema(
    columns={
        "interaction_id": Column(int, nullable=False),
        "user_id": Column(str, nullable=False),
        "product_id": Column(str, nullable=False),
        "event_type": Column(str, checks=pa.Check.isin(EVENT_TYPES), nullable=False),
        "rating": Column(float, checks=pa.Check.in_range(1, 5), nullable=True),
        "event_time": Column("datetime64[us, UTC]", nullable=False),
        "created_at": Column("datetime64[us, UTC]", nullable=False),
        "updated_at": Column("datetime64[us, UTC]", nullable=False),
        "_extraction_run_id": Column(str, nullable=False),
        "_ingested_at": Column("datetime64[us, UTC]", nullable=False),
    },
    strict=True,
    coerce=True,
)
