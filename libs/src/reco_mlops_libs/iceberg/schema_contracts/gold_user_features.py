"""Schema contract for `gold.user_features` — one row per `user_id`,
storing the most recent `MAX_SEQUENCE_LENGTH` interacted items in
chronological order (oldest first). This is the primary training/serving
input for the GRU sequence model (ADR 0004 — replaces LightGBM as the main
ranker), not a flat aggregate table like `gold_item_features`.

See `sql/gold/gold_user_features.sql` for how the sequence is built.
"""

from __future__ import annotations

from pandera.pandas import Check, Column, DataFrameSchema
from pyiceberg.schema import Schema
from pyiceberg.types import IntegerType, ListType, NestedField, StringType, TimestamptzType

TABLE_IDENTIFIER = "gold.user_features"

# Decided in the Phase 3 kickoff discussion (docs/decisions/0004-...): short
# enough to train fast on CPU, matches bao-cao-ky-thuat.md 7.6's GRU4Rec sizing.
MAX_SEQUENCE_LENGTH = 10

ICEBERG_SCHEMA = Schema(
    NestedField(1, "user_id", StringType(), required=True),
    NestedField(2, "item_sequence", ListType(3, StringType(), element_required=True), required=True),
    NestedField(4, "event_time_sequence", ListType(5, TimestamptzType(), element_required=True), required=True),
    NestedField(6, "sequence_length", IntegerType(), required=True),
    NestedField(7, "last_event_time", TimestamptzType(), required=True),
    NestedField(8, "computed_at", TimestamptzType(), required=True),
)


def _is_sequence_of_len_at_most(max_len: int):
    # `to_pandas()` turns an Arrow list column into a column of numpy
    # arrays (not Python `list`), so check length via duck typing instead
    # of `isinstance(..., list)`.
    return lambda series: series.map(lambda value: hasattr(value, "__len__") and len(value) <= max_len)


PANDERA_SCHEMA = DataFrameSchema(
    columns={
        "user_id": Column(str, nullable=False, unique=True),
        "item_sequence": Column(
            object, checks=Check(_is_sequence_of_len_at_most(MAX_SEQUENCE_LENGTH)), nullable=False
        ),
        "event_time_sequence": Column(
            object, checks=Check(_is_sequence_of_len_at_most(MAX_SEQUENCE_LENGTH)), nullable=False
        ),
        "sequence_length": Column(int, checks=Check.in_range(1, MAX_SEQUENCE_LENGTH), nullable=False),
        "last_event_time": Column("datetime64[us, UTC]", nullable=False),
        "computed_at": Column("datetime64[us, UTC]", nullable=False),
    },
    strict=True,
    coerce=True,
)
