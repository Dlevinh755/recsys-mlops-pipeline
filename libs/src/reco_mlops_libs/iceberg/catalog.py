from __future__ import annotations

from pyiceberg.catalog import Catalog, load_catalog

from reco_mlops_libs.common.env import get_env, require_env

CATALOG_NAME = "reco"


def _catalog_properties() -> dict[str, str]:
    """Connection properties for the Lakekeeper REST Catalog + MinIO.

    Every job that reads or writes Iceberg tables must go through this one
    function so the connection is configured identically everywhere (see
    CLAUDE.md principle 3 — Iceberg is the single source of truth between
    batch layers).
    """
    return {
        "type": "rest",
        "uri": require_env("ICEBERG_CATALOG_URI"),
        "warehouse": require_env("ICEBERG_WAREHOUSE_NAME"),
        "s3.endpoint": require_env("ICEBERG_S3_ENDPOINT"),
        "s3.access-key-id": require_env("ICEBERG_S3_ACCESS_KEY"),
        "s3.secret-access-key": require_env("ICEBERG_S3_SECRET_KEY"),
        "s3.region": get_env("ICEBERG_S3_REGION", "us-east-1"),
        "s3.path-style-access": "true",
    }


def get_catalog() -> Catalog:
    return load_catalog(CATALOG_NAME, **_catalog_properties())


def ensure_namespace(catalog: Catalog, namespace: str) -> None:
    if (namespace,) not in catalog.list_namespaces():
        catalog.create_namespace(namespace)
