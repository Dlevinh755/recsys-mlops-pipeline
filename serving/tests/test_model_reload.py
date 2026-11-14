"""Phase 8 — confirm `serving` picks up a new `production`-aliased model
version **without a restart** (the whole point of `ModelLoader`'s polling
loop, Phase 5). Real infra, no mock: runs against the actual `serving`
HTTP endpoint and a real MLflow registry — not `TestClient`/fakes like
`serving/tests/test_endpoints_smoke.py`.

Runs inside `training-job` (needs `torch`+`mlflow` to create 2 real,
tiny model versions on the fixed-seed fixture from
`tests/model/sample_data/` — same one `tests/model/test_training_smoke.py`
uses), against a `serving` container already running on the same network
(`reco-net`) with a short `MODEL_POLL_INTERVAL_SECONDS` (the CI stack sets
this low — see Jenkinsfile — so the test doesn't wait a full production
poll interval). Not part of `serving/`'s own fast unit test suite on
purpose: it needs `torch`, which `serving`'s image deliberately also has
(to run the model), but this test's *job* is training-adjacent, so it lives
where `tests/model/test_training_smoke.py`'s sibling logic already is.
"""

from __future__ import annotations

import json
import os
import time
import unittest
import urllib.request

from reco_mlops_libs.mlflow_utils.registry_client import get_client, set_production_version
from reco_mlops_libs.ranking.base import REGISTERED_MODEL_NAME  # ADR 0008: reco-mlops-agf

SERVING_BASE_URL = os.environ.get("SERVING_BASE_URL", "http://serving:8000")
POLL_TIMEOUT_SECONDS = 30
POLL_CHECK_INTERVAL_SECONDS = 2


def _health() -> dict:
    with urllib.request.urlopen(f"{SERVING_BASE_URL}/health", timeout=5) as response:
        return json.loads(response.read())


def _wait_until_serving_reports_version(version: str) -> str | None:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    last_seen = None
    while time.monotonic() < deadline:
        last_seen = _health().get("model_version")
        if last_seen == version:
            return last_seen
        time.sleep(POLL_CHECK_INTERVAL_SECONDS)
    return last_seen


def _train_one_tiny_version(mlflow) -> str:
    """Reuses the exact same smoke-test fixture/training path as
    `tests/model/test_train_agf_smoke.py` (AGF-GRU4Rec, ADR 0008), but with
    `register=True` so it becomes a real registry version
    `set_production_version` can point at."""
    from dataclasses import replace

    from jobs.training.train_agf import CONFIG_PATH, run_training
    from reco_mlops_libs.ranking.agf.trainer import MethodConfig
    from tests.model.test_train_agf_smoke import load_sample_rows

    cfg = replace(MethodConfig.from_yaml(CONFIG_PATH), use_text=False, epochs=1, batch_size=64)
    item_rows, user_rows = load_sample_rows()
    result = run_training(item_rows, user_rows, cfg, seed=2023, register=True)
    return result["registered_version"]


class ModelReloadTest(unittest.TestCase):
    def test_serving_picks_up_new_production_version_without_restart(self) -> None:
        import mlflow

        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        client = get_client()

        version_a = _train_one_tiny_version(mlflow)
        version_b = _train_one_tiny_version(mlflow)
        self.assertNotEqual(version_a, version_b)

        set_production_version(REGISTERED_MODEL_NAME, version_a, client=client)
        seen = _wait_until_serving_reports_version(version_a)
        self.assertEqual(seen, version_a, "serving never reported version_a within the poll timeout")

        set_production_version(REGISTERED_MODEL_NAME, version_b, client=client)
        seen = _wait_until_serving_reports_version(version_b)
        self.assertEqual(seen, version_b, "serving never picked up version_b — reload/polling is broken")


if __name__ == "__main__":
    unittest.main()
