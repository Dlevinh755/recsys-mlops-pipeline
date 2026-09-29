"""Ad-hoc query of a Bronze/Silver/Gold Iceberg table through DuckDB —
demonstrates the Phase 2 Definition of Done item "3 Iceberg tables queryable
via DuckDB" without wiring DuckDB to the REST Catalog itself. We only need
the current metadata.json location (from PyIceberg, which already talks to
Lakekeeper) and DuckDB's `iceberg_scan` table function.

Usage (run inside the transform container/network, or with
ICEBERG_*/env vars pointed at localhost if run on the host):

    python scripts/duckdb_query_iceberg.py bronze.products
    python scripts/duckdb_query_iceberg.py silver.interactions "SELECT count(*) FROM t"
"""

from __future__ import annotations

import sys

import duckdb

from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.common.env import require_env


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <namespace.table> [sql using 't']", file=sys.stderr)
        return 2
    identifier = sys.argv[1]
    query = sys.argv[2] if len(sys.argv) > 2 else "SELECT * FROM t"

    catalog = get_catalog()
    table = catalog.load_table(identifier)
    metadata_location = table.metadata_location

    def _sql_literal(value: str) -> str:
        # DuckDB's CREATE SECRET is DDL and does not accept `?`/`%s` bound
        # parameters — these values come from our own trusted env vars, not
        # user input, so a single-quote escape is sufficient here.
        return "'" + value.replace("'", "''") + "'"

    endpoint = require_env("ICEBERG_S3_ENDPOINT").removeprefix("http://").removeprefix("https://")
    access_key = require_env("ICEBERG_S3_ACCESS_KEY")
    secret_key = require_env("ICEBERG_S3_SECRET_KEY")
    region = require_env("ICEBERG_S3_REGION")

    con = duckdb.connect()
    con.execute("INSTALL iceberg; LOAD iceberg;")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(
        f"""
        CREATE SECRET reco_minio (
            TYPE s3,
            ENDPOINT {_sql_literal(endpoint)},
            KEY_ID {_sql_literal(access_key)},
            SECRET {_sql_literal(secret_key)},
            REGION {_sql_literal(region)},
            URL_STYLE 'path',
            USE_SSL false
        )
        """
    )
    con.execute(f"CREATE VIEW t AS SELECT * FROM iceberg_scan({_sql_literal(metadata_location)})")
    print(con.execute(query).fetch_df().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
