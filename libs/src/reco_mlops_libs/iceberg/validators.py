"""Enforce a schema contract on a DataFrame before it is committed to
Iceberg — see CLAUDE.md principle 4. Without this step a job with a logic
bug can silently write bad rows into a downstream table.
"""

from __future__ import annotations

import pandas as pd
from pandera.errors import SchemaErrors
from pandera.pandas import DataFrameSchema


class SchemaContractViolation(Exception):
    def __init__(self, table_identifier: str, failure_cases: pd.DataFrame) -> None:
        self.table_identifier = table_identifier
        self.failure_cases = failure_cases
        super().__init__(
            f"{len(failure_cases)} row(s) violate the schema contract for "
            f"{table_identifier!r}:\n{failure_cases.to_string()}"
        )


def validate(df: pd.DataFrame, schema: DataFrameSchema, *, table_identifier: str) -> pd.DataFrame:
    """Validate `df` against `schema`, raising with every failing row at
    once (not just the first) so a job author can fix all violations in one
    pass instead of one-by-one.
    """
    try:
        return schema.validate(df, lazy=True)
    except SchemaErrors as exc:
        raise SchemaContractViolation(table_identifier, exc.failure_cases) from exc
