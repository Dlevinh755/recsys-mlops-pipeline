"""Schema contract for `silver.products` — one row per `product_id`
(deduplicated from `bronze.products`, see `sql/silver/silver_products.sql`).
Lineage columns are dropped here: Silver represents current entity state,
not ingestion history (that stays in Bronze).
"""

from __future__ import annotations

from pandera.pandas import Check, Column, DataFrameSchema
from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType,
    DecimalType,
    NestedField,
    StringType,
    TimestamptzType,
)

TABLE_IDENTIFIER = "silver.products"

ICEBERG_SCHEMA = Schema(
    NestedField(1, "product_id", StringType(), required=True),
    NestedField(2, "title", StringType(), required=True),
    NestedField(3, "category", StringType(), required=True),
    NestedField(4, "brand", StringType(), required=False),
    NestedField(5, "price", DecimalType(12, 2), required=False),
    NestedField(6, "description", StringType(), required=False),
    NestedField(7, "is_active", BooleanType(), required=True),
    NestedField(8, "created_at", TimestamptzType(), required=True),
    NestedField(9, "updated_at", TimestamptzType(), required=True),
    NestedField(10, "image_url", StringType(), required=False),
)

PANDERA_SCHEMA = DataFrameSchema(
    columns={
        "product_id": Column(str, nullable=False, unique=True),
        "title": Column(str, nullable=False),
        "category": Column(str, nullable=False),
        "brand": Column(str, nullable=True),
        "price": Column(float, checks=Check.ge(0), nullable=True),
        "description": Column(str, nullable=True),
        "is_active": Column(bool, nullable=False),
        "created_at": Column("datetime64[us, UTC]", nullable=False),
        "updated_at": Column("datetime64[us, UTC]", nullable=False),
        "image_url": Column(str, nullable=True),
    },
    strict=True,
    coerce=True,
)
