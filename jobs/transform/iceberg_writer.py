"""Shared PyIceberg commit helpers for bronze/silver/gold — see CLAUDE.md
principle 3 (Iceberg is the only source of truth between batch layers) and
principle 4 (every table has an explicit schema contract).

`build_bronze.py`, `build_silver.py`, `build_gold.py` all go through this
module instead of calling PyIceberg's `Catalog`/`Table` APIs directly, so a
future change to how tables are created/committed only has one place to
change.
"""

from __future__ import annotations

import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.schema import Schema
from pyiceberg.table import Table

from reco_mlops_libs.iceberg.catalog import ensure_namespace


def get_or_create_table(catalog: Catalog, identifier: str, iceberg_schema: Schema) -> Table:
    namespace = identifier.split(".", 1)[0]
    ensure_namespace(catalog, namespace)
    return catalog.create_table_if_not_exists(identifier, schema=iceberg_schema)


def committed_run_ids(table: Table, lineage_column: str = "_extraction_run_id") -> set[str]:
    """Return the set of lineage ids already committed into this Bronze
    table (`_extraction_run_id` — since
    docs/decisions/0006-dlt-native-incremental-extract.md, a dlt
    `_dlt_load_id`, not a Postgres `extraction_runs.run_id`).

    This makes `build_bronze.py` idempotent without a second bookkeeping
    table: Iceberg's own data is the only source of truth for "has this load
    already been committed" (CLAUDE.md principle 3). A crash between an
    Iceberg append and any follow-up step cannot desync two systems, because
    there is no follow-up step — the append either landed in a snapshot
    (visible here) or it did not.
    """
    if table.current_snapshot() is None:
        return set()
    arrow = table.scan(selected_fields=(lineage_column,)).to_arrow()
    return set(arrow[lineage_column].to_pylist())


def fill_missing_optional_columns(data: pa.Table, iceberg_schema: Schema) -> pa.Table:
    """Add back nullable columns that are entirely absent from `data`.

    `dlt` cannot infer a column's type when every value in a batch is NULL
    (e.g. an `interactions` batch with no `rating` events) and silently
    drops that column from the Parquet file instead of writing it as
    all-null — encountered for real while testing with larger simulated
    batches (see `docs/modules/phase-2-lakehouse.md`). A column missing for
    this reason is not a data problem, so we restore it as all-null here,
    before schema validation runs. A *required* column that is genuinely
    missing is left alone — that should fail validation loudly, not be
    papered over.
    """
    arrow_schema = iceberg_schema.as_arrow()
    for field in arrow_schema:
        if field.name in data.column_names or not field.nullable:
            continue
        data = data.append_column(field.name, pa.nulls(data.num_rows, type=field.type))
    return data


def cast_to_arrow_schema(data: pa.Table, arrow_schema: pa.Schema) -> pa.Table:
    """Reorder/cast an in-memory Arrow table to exactly match `arrow_schema`.

    Two dlt batches from the same Phase 1 run can otherwise disagree on
    column order or a nullable column's concrete type (e.g. `string` vs
    `large_string` when one batch happened to have every value NULL) — see
    `docs/modules/phase-1-extract-watermark.md`. Normalizing every batch to
    one canonical schema before `pa.concat_tables` avoids that mismatch.
    """
    return data.select(arrow_schema.names).cast(arrow_schema)


def cast_to_table_schema(data: pa.Table, table: Table) -> pa.Table:
    """Reorder/cast an in-memory Arrow table to exactly match the Iceberg
    table's current Arrow schema, so `append`/`overwrite` never fails on a
    harmless column-order or nullability mismatch.
    """
    return cast_to_arrow_schema(data, table.schema().as_arrow())


def append(table: Table, data: pa.Table) -> None:
    table.append(cast_to_table_schema(data, table))


def overwrite(table: Table, data: pa.Table) -> None:
    """Replace the table's entire contents with `data` in one snapshot.

    Used by Silver/Gold, which are fully recomputed from Bronze/Silver each
    run at MVP scale (see `docs/modules/phase-2-lakehouse.md`), rather than
    merged incrementally.
    """
    table.overwrite(cast_to_table_schema(data, table))
