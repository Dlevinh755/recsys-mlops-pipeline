"""Declares each Phase 1 source table's identity for the dlt-native
incremental extract in `jobs/extract/dlt_writer.py`.

This module used to be `watermark.py` and also owned a hand-rolled Postgres
watermark/run-bookkeeping mechanism. That mechanism is gone — `dlt`'s own
incremental cursor + pipeline state now owns correctness. See
`docs/decisions/0006-dlt-native-incremental-extract.md` for why.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpec:
    name: str
    table: str
    primary_key: str
    primary_key_type: str
