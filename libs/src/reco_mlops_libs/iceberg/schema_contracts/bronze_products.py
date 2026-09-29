"""Schema contract for `bronze.products`.

Bronze keeps the source row shape almost untouched (append-only, no dedup),
plus two lineage columns that tie every row back to the Phase 1 extraction
run it came from — needed for idempotent re-runs of `build_bronze.py`
(see `jobs/transform/build_bronze.py`).
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema
from pyiceberg.schema import Schema
from pyiceberg.types import (
    BooleanType,
    DecimalType,
    NestedField,
    StringType,
    TimestamptzType,
)

TABLE_IDENTIFIER = "bronze.products"

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
    NestedField(10, "_extraction_run_id", StringType(), required=True),
    NestedField(11, "_ingested_at", TimestamptzType(), required=True),
    NestedField(12, "image_url", StringType(), required=False),
)

PANDERA_SCHEMA = DataFrameSchema(
    columns={
        "product_id": Column(str, nullable=False),
        "title": Column(str, nullable=False),
        "category": Column(str, nullable=False),
        "brand": Column(str, nullable=True),
        "price": Column(float, checks=pa.Check.ge(0), nullable=True),
        "description": Column(str, nullable=True),
        "is_active": Column(bool, nullable=False),
        "created_at": Column("datetime64[us, UTC]", nullable=False),
        "updated_at": Column("datetime64[us, UTC]", nullable=False),
        "_extraction_run_id": Column(str, nullable=False),
        "_ingested_at": Column("datetime64[us, UTC]", nullable=False),
        "image_url": Column(str, nullable=True),
    },
    strict=True,
    coerce=True,
)
