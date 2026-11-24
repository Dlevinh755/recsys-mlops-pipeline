"""Regression test for docs/decisions/0009-pin-s3fs-remote-signing.md.

s3fs >= 2026.2.0 silently replaces its botocore client inside
`S3FileSystem.set_session()` once the old client's aiohttp sessions are
closed. PyIceberg 0.12.0 registers Lakekeeper's remote signer
(`before-sign.s3`) only on the client that existed when the filesystem was
built, so the replacement client sends requests **unsigned** -> MinIO 403
(`dag_transform.build_bronze` failed exactly like this, 2026-11-24).

This test forces that situation against the real Lakekeeper + MinIO: it
closes the client's aiohttp sessions and asserts s3fs keeps the same client
(the pinned 2026.1.0 does not auto-refresh; >= 2026.2.0 does). Uses s3fs
private attributes on purpose: the closed-session path is the bug guarded.

Run with `make test-integration`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa
import pytest
from fsspec.asyn import sync
from pyiceberg.schema import Schema
from pyiceberg.types import NestedField, StringType, TimestamptzType

from jobs.transform.iceberg_writer import append, get_or_create_table
from reco_mlops_libs.iceberg.catalog import get_catalog

TEST_IDENTIFIER = "bronze.it_test_remote_signing"

TEST_SCHEMA = Schema(
    NestedField(1, "product_id", StringType(), required=True),
    NestedField(2, "_ingested_at", TimestamptzType(), required=True),
)


@pytest.fixture
def table():
    catalog = get_catalog()
    if catalog.table_exists(TEST_IDENTIFIER):
        catalog.purge_table(TEST_IDENTIFIER)
    tbl = get_or_create_table(catalog, TEST_IDENTIFIER, TEST_SCHEMA)
    append(tbl, pa.table({
        "product_id": ["p1"],
        "_ingested_at": pa.array([datetime.now(timezone.utc)], type=pa.timestamp("us", tz="UTC")),
    }))
    yield catalog.load_table(TEST_IDENTIFIER)
    catalog.purge_table(TEST_IDENTIFIER)


def test_s3fs_does_not_replace_client_when_http_session_closed(table):
    """The signer hook lives only on the client PyIceberg created, so s3fs
    must never swap that client for a new one behind PyIceberg's back."""
    manifest_list = table.current_snapshot().manifest_list
    io = table.io
    assert io.new_input(manifest_list).exists()

    fs = io.get_fs("s3")
    original_client = fs._s3
    http_session = original_client._endpoint.http_session
    try:
        for session in list((http_session._sessions or {}).values()):
            sync(fs.loop, session.close)

        # s3fs >= 2026.2.0 rebuilds the client here, dropping the
        # `before-sign.s3` hook -> later requests are unsigned -> MinIO 403.
        sync(fs.loop, fs.set_session)
        assert fs._s3 is original_client
    finally:
        # We just closed this filesystem's HTTP sessions on purpose. fsspec
        # caches S3FileSystem instances per process, so drop the cache or
        # every later test in the same run would reuse the closed one.
        type(fs).clear_instance_cache()
