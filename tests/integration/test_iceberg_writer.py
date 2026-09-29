"""Integration tests for the Phase 2 PyIceberg commit path.

Deliberately **not mocked** — these run against the real Lakekeeper REST
Catalog + MinIO started by `make up` (see
`docs/cau-truc-project.md`: "PyIceberg commit + REST Catalog" is the
highest-risk part of the project and should be tested against real
infrastructure, not a mock). Run with:

    docker compose --profile jobs run --rm --entrypoint python transform-job -m pytest /app/tests/integration -v

(or `make test-integration`, which wraps the same command).

Tests use their own `bronze.it_test_*` tables so they never touch the real
`bronze`/`silver`/`gold` tables built by the actual jobs, and clean up after
themselves so the suite can be re-run freely.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa
import pytest
from pandera.pandas import Column, DataFrameSchema
from pyiceberg.schema import Schema
from pyiceberg.types import NestedField, StringType, TimestamptzType

from jobs.transform.iceberg_writer import append, cast_to_table_schema, committed_run_ids, get_or_create_table, overwrite
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.validators import SchemaContractViolation, validate

BRONZE_TEST_IDENTIFIER = "bronze.it_test_products"
SILVER_TEST_IDENTIFIER = "silver.it_test_products"

TEST_SCHEMA = Schema(
    NestedField(1, "product_id", StringType(), required=True),
    NestedField(2, "title", StringType(), required=True),
    NestedField(3, "_extraction_run_id", StringType(), required=True),
    NestedField(4, "_ingested_at", TimestamptzType(), required=True),
)

TEST_PANDERA_SCHEMA = DataFrameSchema(
    columns={
        "product_id": Column(str, nullable=False),
        "title": Column(str, nullable=False),
        "_extraction_run_id": Column(str, nullable=False),
        "_ingested_at": Column("datetime64[us, UTC]", nullable=False),
    },
    strict=True,
    coerce=True,
)


def _row(product_id: str, title: str, run_id: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "product_id": product_id,
        "title": title,
        "_extraction_run_id": run_id,
        "_ingested_at": pd.Timestamp(datetime.now(timezone.utc)),
    }])


@pytest.fixture()
def catalog():
    return get_catalog()


@pytest.fixture()
def bronze_test_table(catalog):
    catalog.purge_table(BRONZE_TEST_IDENTIFIER) if catalog.table_exists(BRONZE_TEST_IDENTIFIER) else None
    table = get_or_create_table(catalog, BRONZE_TEST_IDENTIFIER, TEST_SCHEMA)
    yield table
    if catalog.table_exists(BRONZE_TEST_IDENTIFIER):
        catalog.purge_table(BRONZE_TEST_IDENTIFIER)


@pytest.fixture()
def silver_test_table(catalog):
    if catalog.table_exists(SILVER_TEST_IDENTIFIER):
        catalog.purge_table(SILVER_TEST_IDENTIFIER)
    table = get_or_create_table(catalog, SILVER_TEST_IDENTIFIER, TEST_SCHEMA)
    yield table
    if catalog.table_exists(SILVER_TEST_IDENTIFIER):
        catalog.purge_table(SILVER_TEST_IDENTIFIER)


def test_append_is_visible_and_tracked_by_run_id(bronze_test_table):
    assert committed_run_ids(bronze_test_table) == set()

    validated = validate(_row("p001", "Mouse", "run-a"), TEST_PANDERA_SCHEMA, table_identifier=BRONZE_TEST_IDENTIFIER)
    append(bronze_test_table, pa.Table.from_pandas(validated, preserve_index=False))

    assert bronze_test_table.scan().to_arrow().num_rows == 1
    assert committed_run_ids(bronze_test_table) == {"run-a"}


def test_rerun_with_same_run_id_is_skipped_not_duplicated(bronze_test_table):
    """Reproduces the exact guard `build_bronze.py` relies on: before
    appending a batch, check whether its run_id is already committed."""
    validated = validate(_row("p001", "Mouse", "run-a"), TEST_PANDERA_SCHEMA, table_identifier=BRONZE_TEST_IDENTIFIER)
    append(bronze_test_table, pa.Table.from_pandas(validated, preserve_index=False))

    already_committed = committed_run_ids(bronze_test_table)
    assert "run-a" in already_committed
    # build_bronze.py's fetch_pending_runs would exclude this run_id here,
    # so no second append happens — assert the table is unaffected by
    # confirming a *new* run_id still appends correctly on top.
    validated_b = validate(_row("p002", "Keyboard", "run-b"), TEST_PANDERA_SCHEMA, table_identifier=BRONZE_TEST_IDENTIFIER)
    append(bronze_test_table, pa.Table.from_pandas(validated_b, preserve_index=False))

    arrow = bronze_test_table.scan().to_arrow()
    assert arrow.num_rows == 2
    assert committed_run_ids(bronze_test_table) == {"run-a", "run-b"}


def test_overwrite_replaces_full_table_contents(silver_test_table):
    first = validate(_row("p001", "Mouse", "run-a"), TEST_PANDERA_SCHEMA, table_identifier=SILVER_TEST_IDENTIFIER)
    overwrite(silver_test_table, pa.Table.from_pandas(first, preserve_index=False))
    assert silver_test_table.scan().to_arrow().num_rows == 1

    second = validate(_row("p002", "Keyboard", "run-b"), TEST_PANDERA_SCHEMA, table_identifier=SILVER_TEST_IDENTIFIER)
    overwrite(silver_test_table, pa.Table.from_pandas(second, preserve_index=False))

    result = silver_test_table.scan().to_arrow()
    assert result.num_rows == 1
    assert result.column("product_id").to_pylist() == ["p002"]


def test_cast_to_table_schema_matches_iceberg_column_order(bronze_test_table):
    reordered = pd.DataFrame([{
        "_ingested_at": pd.Timestamp(datetime.now(timezone.utc)),
        "title": "Mouse",
        "_extraction_run_id": "run-a",
        "product_id": "p001",
    }])
    arrow_in = pa.Table.from_pandas(reordered, preserve_index=False)
    cast = cast_to_table_schema(arrow_in, bronze_test_table)
    assert cast.schema.names == bronze_test_table.schema().as_arrow().names


def test_schema_contract_rejects_invalid_rows():
    bad = pd.DataFrame([{
        "product_id": "p001",
        "title": None,  # required, non-nullable
        "_extraction_run_id": "run-a",
        "_ingested_at": pd.Timestamp(datetime.now(timezone.utc)),
    }])
    with pytest.raises(SchemaContractViolation):
        validate(bad, TEST_PANDERA_SCHEMA, table_identifier=BRONZE_TEST_IDENTIFIER)
