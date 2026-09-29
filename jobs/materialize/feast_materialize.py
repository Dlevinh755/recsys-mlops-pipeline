"""Phase 3 — `feast apply` + `feast materialize-incremental` against
`feature_repo/`, pushing the latest Parquet snapshot
(`jobs/materialize/export_to_parquet.py`'s output) into Redis.

Shells out to the `feast` CLI directly rather than re-implementing repo
parsing in Python — this is also literally the Phase 3 Definition of Done
wording in `docs/de-xuat-trien-khai.md` ("feast apply chạy được ... feast
materialize đẩy dữ liệu vào Redis").
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

FEATURE_REPO_DIR = Path(__file__).resolve().parents[2] / "feature_repo"


def run_feast(*args: str) -> None:
    subprocess.run(["feast", *args], cwd=FEATURE_REPO_DIR, check=True)


def main() -> int:
    run_feast("apply")
    end_time = datetime.now(timezone.utc).isoformat()
    run_feast("materialize-incremental", end_time)
    print(json.dumps({"feast_materialize_result": {"materialized_to": end_time}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
