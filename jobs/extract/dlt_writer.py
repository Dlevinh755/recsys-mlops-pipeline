"""Phase 1 extract resource — dlt-native incremental read of one Postgres
source table.

Replaces the hand-rolled Postgres watermark/run bookkeeping (formerly
`jobs/extract/watermark.py`) with `dlt.sources.incremental`. The cursor's
advance is now tied to a fully successful `pipeline.run()` (extract ->
normalize -> load) — `jobs/extract/run.py` passes
`restore_from_destination=True`, so the next run's `updated_at.last_value`
is restored from what actually landed on MinIO (`extract-staging`), never
from a side bookkeeping table that could disagree with it.

If this generator raises partway through, dlt's extract step fails before
normalize/load ever run: nothing new lands on MinIO, and the failed run's
own load id never gets minted. The next run's `last_value` is therefore
unchanged from the last *successful* run, and it re-reads everything from
there in one shot. This closes the gap the old design had (see
docs/decisions/0006-dlt-native-incremental-extract.md): a Postgres watermark
advanced per internal batch could be ahead of what `build_bronze.py` — gated
on the parent run's overall status — would ever load.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterator

import dlt
import psycopg
from psycopg import sql

from jobs.extract.source_spec import SourceSpec

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _first_page_query(source: SourceSpec) -> sql.Composed:
    """Strict lower bound (`>`) on the run's restored `last_value`.

    Tried `>=` plus `dlt.sources.incremental(..., primary_key=...)` to let
    dlt dedup rows tied at the exact boundary value first — empirically,
    with this dlt version + the `filesystem` destination, that cross-run
    dedup did not hold (confirmed by running extraction twice with no
    source changes: the second run re-yielded every row sharing the bulk
    seed's `updated_at` value instead of zero rows). `>` alone is safe
    *without* that dedup because pagination within one run is exhaustive —
    `_next_page_query` below loops until a short/empty page, so every row
    tied at a given `updated_at` is always captured within the run that
    first reaches it. A later run can therefore safely skip anything at or
    before the previous run's final `updated_at`.

    Trade-off: a row inserted later with `updated_at` exactly equal to an
    already-fully-processed timestamp would be silently missed. Only bulk,
    single-timestamp seed loads hit this in practice (e.g. the Amazon
    dataset seed) — real incremental writes get distinct per-row
    timestamps (`simulate_incremental_update.py`, the `/interact`
    endpoint), so this does not affect normal incremental operation.
    """
    table = sql.Identifier(source.table)
    pk = sql.Identifier(source.primary_key)
    return sql.SQL(
        "SELECT * FROM {} WHERE updated_at > %s ORDER BY updated_at, {} LIMIT %s"
    ).format(table, pk)


def _next_page_query(source: SourceSpec) -> sql.Composed:
    """Strict row-comparison continuation for pages after the first one,
    *within the same run*. No ties are possible here — `primary_key` is
    unique and ordering is deterministic — so `>` (not `>=`) is correct.
    """
    table = sql.Identifier(source.table)
    pk = sql.Identifier(source.primary_key)
    return sql.SQL(
        "SELECT * FROM {} WHERE (updated_at, {}) > (%s, %s::{}) "
        "ORDER BY updated_at, {} LIMIT %s"
    ).format(table, pk, sql.SQL(source.primary_key_type), pk)


def make_source_resource(
    source: SourceSpec,
    dsn: str,
    page_size: int,
    fail_after_rows: tuple[str, int] | None = None,
) -> tuple[Any, dict[str, int]]:
    """Build one dlt resource that reads `source.table` incrementally by
    `updated_at`, paging internally in chunks of `page_size` rows.

    Returns `(resource, counter)` — `counter["rows"]` is updated as rows are
    yielded, so `jobs/extract/run.py` can log/return a row count without
    depending on dlt's internal load-metrics shape.

    `fail_after_rows`, when it names this `source`, raises once that many
    rows have been yielded — used to prove the crash-mid-extraction
    guarantee described in the module docstring (replaces the old
    `EXTRACT_FAIL_AFTER_WRITE` injection point).
    """
    counter = {"rows": 0}
    limit = fail_after_rows[1] if fail_after_rows and fail_after_rows[0] == source.name else None

    @dlt.resource(name=source.name, write_disposition="append")
    def rows(
        updated_at: dlt.sources.incremental = dlt.sources.incremental(
            "updated_at", initial_value=EPOCH
        ),
    ) -> Iterator[list[dict[str, Any]]]:
        cursor: tuple[datetime, str] | None = None
        with psycopg.connect(dsn) as conn:
            while True:
                if cursor is None:
                    query = _first_page_query(source)
                    params: tuple[Any, ...] = (updated_at.last_value, page_size)
                else:
                    query = _next_page_query(source)
                    params = (cursor[0], cursor[1], page_size)
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    columns = [column.name for column in cur.description or []]
                    page = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
                if not page:
                    break
                last = page[-1]
                cursor = (last["updated_at"], str(last[source.primary_key]))
                if limit is not None and counter["rows"] + len(page) >= limit:
                    keep = limit - counter["rows"]
                    counter["rows"] += keep
                    yield page[:keep]
                    raise RuntimeError(
                        f"Injected failure after {limit} rows for source={source.name}"
                    )
                counter["rows"] += len(page)
                print(json.dumps({
                    "source": source.name,
                    "page_rows": len(page),
                    "page_cursor_to": cursor[1],
                }))
                yield page
                if len(page) < page_size:
                    break

    return rows(), counter
