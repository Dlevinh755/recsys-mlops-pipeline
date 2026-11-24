"""Integration tests for incremental Silver (docs/decisions/0010-silver-incremental.md).

Run against the real Lakekeeper + MinIO like `test_iceberg_writer.py`, on
their own `bronze.it_test_si_*` / `silver.it_test_si_*` tables (real
Bronze/Silver schemas) so the real tables are never touched:

    docker compose --profile jobs run --rm --entrypoint python transform-job -m pytest /app/tests/integration -v

The most important test is `test_incremental_equals_full_refresh`: PyIceberg
0.12's `upsert` is young, so equivalence with a full recompute is the safety
net.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pyarrow as pa
import pytest
from pyiceberg.table import Transaction

from jobs.transform.build_silver import sync_silver_table
from jobs.transform.iceberg_writer import (
    SILVER_WATERMARK_PROPERTY,
    append,
    get_or_create_table,
    read_watermark,
    upsert_with_watermark,
)
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import (
    bronze_interactions,
    bronze_products,
    silver_interactions,
    silver_products,
)

BASE = datetime(2026, 11, 1, tzinfo=timezone.utc)


def t(minutes: int) -> datetime:
    return BASE + timedelta(minutes=minutes)


def _fresh(catalog, identifier, schema):
    if catalog.table_exists(identifier):
        catalog.purge_table(identifier)
    return get_or_create_table(catalog, identifier, schema)


@pytest.fixture()
def tables():
    """bronze / silver / silver_ref (for full-refresh comparison), interactions."""
    catalog = get_catalog()
    created = {
        "bronze": _fresh(catalog, "bronze.it_test_si_interactions", bronze_interactions.ICEBERG_SCHEMA),
        "silver": _fresh(catalog, "silver.it_test_si_interactions", silver_interactions.ICEBERG_SCHEMA),
        "ref": _fresh(catalog, "silver.it_test_si_interactions_ref", silver_interactions.ICEBERG_SCHEMA),
    }
    yield created
    for name in ("bronze.it_test_si_interactions", "silver.it_test_si_interactions",
                 "silver.it_test_si_interactions_ref"):
        if catalog.table_exists(name):
            catalog.purge_table(name)


def add(bronze, rows: list[dict], ingested_at: datetime) -> None:
    """Append Bronze interaction rows; one `_ingested_at` per call, like build_bronze."""
    full = [
        {
            "interaction_id": r["id"],
            "user_id": r.get("user", "u1"),
            "product_id": r.get("product", "p1"),
            "event_type": r.get("event", "rating"),
            "rating": r.get("rating", Decimal("4.0")),
            "event_time": t(0),
            "created_at": t(0),
            "updated_at": t(r["upd"]),
            "_extraction_run_id": f"run-{ingested_at.isoformat()}",
            "_ingested_at": ingested_at,
        }
        for r in rows
    ]
    append(bronze, pa.Table.from_pylist(full, schema=bronze_interactions.ICEBERG_SCHEMA.as_arrow()))


def sync(bronze, silver, **kw) -> dict:
    return sync_silver_table(
        bronze, silver, "bronze_interactions", "silver_interactions.sql", silver_interactions,
        "interaction_id", **kw,
    )


def rows(table) -> list[dict]:
    return sorted(table.scan().to_arrow().to_pylist(), key=lambda r: r["interaction_id"])


def n_snapshots(table) -> int:
    return len(table.metadata.snapshots)


def refresh(table):
    return get_catalog().load_table(table.name())


def test_first_run_equals_full_recompute_and_sets_watermark(tables):
    b, s = tables["bronze"], tables["silver"]
    add(b, [{"id": 1, "upd": 1}, {"id": 2, "upd": 1}], t(10))
    add(b, [{"id": 2, "upd": 5, "rating": Decimal("2.0")}, {"id": 3, "upd": 1}], t(20))

    result = sync(b, s)

    assert result["mode"] == "full"
    assert result["watermark_before"] is None
    assert [r["interaction_id"] for r in rows(refresh(s))] == [1, 2, 3]
    assert rows(refresh(s))[1]["rating"] == Decimal("2.0")  # latest updated_at wins
    assert read_watermark(refresh(s)) == t(20).isoformat(timespec="microseconds")
    assert result["watermark_after"] == read_watermark(refresh(s))


def test_no_new_data_does_not_commit(tables):
    b, s = tables["bronze"], tables["silver"]
    add(b, [{"id": 1, "upd": 1}], t(10))
    sync(b, s)
    before = n_snapshots(refresh(s))

    result = sync(b, refresh(s))

    assert result["mode"] == "incremental"
    assert result["bronze_rows_scanned"] == 0
    assert result["rows_upserted"] == 0
    assert n_snapshots(refresh(s)) == before


def test_only_new_rows_are_inserted_old_ones_untouched(tables):
    b, s = tables["bronze"], tables["silver"]
    add(b, [{"id": 1, "upd": 1}, {"id": 2, "upd": 1}], t(10))
    sync(b, s)
    old = rows(refresh(s))

    add(b, [{"id": 3, "upd": 1}], t(20))
    result = sync(b, refresh(s))

    assert (result["rows_inserted"], result["rows_updated"], result["bronze_rows_scanned"]) == (1, 0, 1)
    assert rows(refresh(s))[:2] == old
    assert [r["interaction_id"] for r in rows(refresh(s))] == [1, 2, 3]


def test_newer_version_updates_silver(tables):
    b, s = tables["bronze"], tables["silver"]
    add(b, [{"id": 1, "upd": 1, "rating": Decimal("3.0")}], t(10))
    sync(b, s)

    add(b, [{"id": 1, "upd": 9, "rating": Decimal("5.0")}], t(20))
    result = sync(b, refresh(s))

    assert (result["rows_inserted"], result["rows_updated"]) == (0, 1)
    assert rows(refresh(s))[0]["rating"] == Decimal("5.0")
    assert rows(refresh(s))[0]["updated_at"] == t(9)


def test_older_version_is_skipped_and_counted(tables):
    b, s = tables["bronze"], tables["silver"]
    add(b, [{"id": 1, "upd": 9, "rating": Decimal("5.0")}], t(10))
    sync(b, s)

    add(b, [{"id": 1, "upd": 2, "rating": Decimal("1.0")}, {"id": 2, "upd": 1}], t(20))
    result = sync(b, refresh(s))

    assert result["rows_skipped_stale"] == 1
    assert (result["rows_inserted"], result["rows_updated"]) == (1, 0)
    assert rows(refresh(s))[0]["rating"] == Decimal("5.0")


def test_all_stale_delta_only_moves_the_watermark(tables):
    b, s = tables["bronze"], tables["silver"]
    add(b, [{"id": 1, "upd": 9}], t(10))
    sync(b, s)
    before = n_snapshots(refresh(s))

    add(b, [{"id": 1, "upd": 2}], t(20))
    result = sync(b, refresh(s))

    assert result["rows_skipped_stale"] == 1 and result["rows_upserted"] == 0
    assert read_watermark(refresh(s)) == t(20).isoformat(timespec="microseconds")
    assert n_snapshots(refresh(s)) == before  # property-only commit, no data snapshot


def test_duplicate_keys_in_one_delta_keep_latest(tables):
    b, s = tables["bronze"], tables["silver"]
    add(b, [{"id": 1, "upd": 1}], t(10))
    sync(b, s)

    # by updated_at
    add(b, [{"id": 1, "upd": 3, "rating": Decimal("2.0")}, {"id": 1, "upd": 7, "rating": Decimal("4.0")}], t(20))
    # tie on updated_at -> most recently ingested wins
    add(b, [{"id": 2, "upd": 5, "rating": Decimal("1.0")}], t(21))
    add(b, [{"id": 2, "upd": 5, "rating": Decimal("3.0")}], t(22))
    sync(b, refresh(s))

    got = {r["interaction_id"]: r for r in rows(refresh(s))}
    assert got[1]["rating"] == Decimal("4.0")
    assert got[2]["rating"] == Decimal("3.0")


def test_incremental_equals_full_refresh(tables):
    """Many Bronze loads with inserts, updates, stale and duplicate rows,
    NULL ratings: after every incremental run Silver must equal a full
    recompute over the same Bronze."""
    b, s, ref = tables["bronze"], tables["silver"], tables["ref"]
    steps = [
        (10, [{"id": i, "upd": 1} for i in range(1, 6)]),
        (20, [{"id": 2, "upd": 4, "rating": None}, {"id": 6, "upd": 1}]),
        (30, [{"id": 3, "upd": 0, "rating": Decimal("1.0")}]),  # stale
        (40, [{"id": 4, "upd": 8}, {"id": 4, "upd": 9, "rating": Decimal("5.0")}, {"id": 7, "upd": 2}]),
        (50, [{"id": 2, "upd": 4, "rating": None}]),  # identical re-extract (rating NULL)
        (60, [{"id": 1, "upd": 3, "rating": None}, {"id": 8, "upd": 1, "rating": None}]),
    ]
    s_live = s
    for minute, batch in steps:
        add(b, batch, t(minute))
        sync(b, s_live)
        s_live = refresh(s)
        ref = refresh(ref)
        sync(b, ref, full_refresh=True)
        assert rows(refresh(s)) == rows(refresh(ref)), f"diverged after load at t+{minute}"

    assert [r["rating"] for r in rows(refresh(s)) if r["interaction_id"] == 2] == [None]


def test_null_rating_is_not_a_change(tables):
    """A row whose `rating` is NULL must not look 'changed' on every upsert:
    re-upserting the identical row updates nothing."""
    b, s = tables["bronze"], tables["silver"]
    add(b, [{"id": 1, "upd": 1, "rating": None}], t(10))
    sync(b, s)
    s = refresh(s)
    same = pa.Table.from_pylist(rows(s), schema=silver_interactions.ICEBERG_SCHEMA.as_arrow())

    result = upsert_with_watermark(s, same, ["interaction_id"], t(99).isoformat(timespec="microseconds"))

    assert (result.rows_updated, result.rows_inserted) == (0, 0)


def test_failed_upsert_leaves_watermark_unchanged(tables, monkeypatch):
    b, s = tables["bronze"], tables["silver"]
    add(b, [{"id": 1, "upd": 1}], t(10))
    sync(b, s)
    wm = read_watermark(refresh(s))
    snapshots = n_snapshots(refresh(s))

    add(b, [{"id": 2, "upd": 1}], t(20))
    with monkeypatch.context() as m:
        def boom(self, *args, **kwargs):
            raise RuntimeError("simulated upsert failure")
        m.setattr(Transaction, "upsert", boom)
        with pytest.raises(RuntimeError):
            sync(b, refresh(s))

    assert read_watermark(refresh(s)) == wm
    assert n_snapshots(refresh(s)) == snapshots
    assert [r["interaction_id"] for r in rows(refresh(s))] == [1]

    result = sync(b, refresh(s))  # re-run still sees the whole delta
    assert result["rows_inserted"] == 1
    assert [r["interaction_id"] for r in rows(refresh(s))] == [1, 2]


def test_products_use_product_id_key(tables):
    catalog = get_catalog()
    b = _fresh(catalog, "bronze.it_test_si_products", bronze_products.ICEBERG_SCHEMA)
    s = _fresh(catalog, "silver.it_test_si_products", silver_products.ICEBERG_SCHEMA)
    try:
        def product(pid, upd, title, ingested):
            return {
                "product_id": pid, "title": title, "category": "c", "brand": None, "price": Decimal("9.99"),
                "description": None, "is_active": True, "created_at": t(0), "updated_at": t(upd),
                "_extraction_run_id": f"r{ingested}", "_ingested_at": t(ingested),
                "image_url": None, "category_path": "a > b",
            }

        def load(prods):
            append(b, pa.Table.from_pylist(prods, schema=bronze_products.ICEBERG_SCHEMA.as_arrow()))

        def run(table, **kw):
            return sync_silver_table(b, table, "bronze_products", "silver_products.sql", silver_products,
                                     "product_id", **kw)

        load([product("p1", 1, "Old", 10), product("p2", 1, "Two", 10)])
        run(s)
        load([product("p1", 5, "New", 20), product("p3", 1, "Three", 20)])
        result = run(refresh(s))

        assert (result["rows_inserted"], result["rows_updated"]) == (1, 1)
        got = {r["product_id"]: r["title"] for r in refresh(s).scan().to_arrow().to_pylist()}
        assert got == {"p1": "New", "p2": "Two", "p3": "Three"}
        assert read_watermark(refresh(s)) == t(20).isoformat(timespec="microseconds")
        assert SILVER_WATERMARK_PROPERTY in refresh(s).properties
    finally:
        for name in ("bronze.it_test_si_products", "silver.it_test_si_products"):
            if catalog.table_exists(name):
                catalog.purge_table(name)
