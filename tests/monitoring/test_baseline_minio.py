"""ADR 0014 — baseline files and drift-report lookup against the real MinIO
(`monitoring-job` image, stack up). Keys live under `it_test_<uuid>/` and are
removed afterwards; MLflow and the Silver table are small fakes (their real
counterparts are exercised by the end-to-end run, see phase-7-monitoring.md)."""

from __future__ import annotations

import json
import unittest
import uuid
from types import SimpleNamespace

import pandas as pd

from jobs.monitoring import baseline
from jobs.monitoring.refresh_baseline import SNAPSHOT_PARAM, refresh
from jobs.monitoring.retrain_decision import latest_drift_summary
from reco_mlops_libs.common.env import require_env


def silver(n: int, offset: int = 0) -> pd.DataFrame:
    return pd.DataFrame({"product_id": [f"p{offset + i}" for i in range(n)], "rating": [4.0] * n})


class FakeTable:
    """Time travel: `snapshots` maps snapshot id → rows; `current` is the latest."""

    def __init__(self, snapshots: dict[int, pd.DataFrame], current: int) -> None:
        self.snapshots, self.current = snapshots, current

    def current_snapshot(self):
        return SimpleNamespace(snapshot_id=self.current)

    def scan(self, snapshot_id: int | None = None, selected_fields=None):
        key = self.current if snapshot_id is None else snapshot_id
        if key not in self.snapshots:
            raise ValueError(f"snapshot {key} expired")
        return SimpleNamespace(to_pandas=lambda: self.snapshots[key])


class FakeClient:
    def __init__(self) -> None:
        self.production: SimpleNamespace | None = None
        self.params: dict[str, dict] = {}

    def serve(self, version: str, snapshot_id: int | None) -> None:
        run_id = f"run{version}"
        self.production = SimpleNamespace(version=version, run_id=run_id)
        self.params[run_id] = {} if snapshot_id is None else {SNAPSHOT_PARAM: str(snapshot_id)}

    def get_model_version_by_alias(self, name: str, alias: str):
        if self.production is None:
            from mlflow.exceptions import MlflowException

            raise MlflowException("no alias")
        return self.production

    def get_run(self, run_id: str):
        return SimpleNamespace(data=SimpleNamespace(params=self.params[run_id]))


class BaselineMinioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fs = baseline.baseline_filesystem()
        cls.baseline_bucket = require_env("MONITORING_BASELINE_BUCKET")
        cls.reports_bucket = require_env("MONITORING_REPORTS_BUCKET")

    def setUp(self) -> None:
        self.prefix = f"it_test_{uuid.uuid4().hex[:8]}/"
        self.job = f"it_test_{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        for path in (f"{self.baseline_bucket}/{self.prefix}", f"{self.reports_bucket}/{self.job}"):
            if self.fs.exists(path):
                self.fs.rm(path, recursive=True)

    def seed_train(self, n: int = 5) -> None:
        with self.fs.open(f"{self.baseline_bucket}/{self.prefix}{baseline.TRAIN_KEY}", "wb") as file:
            pd.DataFrame({"parent_asin": silver(n)["product_id"], "rating": [3.0] * n}).to_parquet(file, index=False)

    def test_falls_back_to_train_parquet_then_follows_current(self) -> None:
        self.seed_train(5)
        frame, info = baseline.load_baseline(self.fs, self.baseline_bucket, self.prefix)
        self.assertEqual((len(frame), info["source"]), (5, "train.parquet"))

        baseline.write_baseline(self.fs, self.baseline_bucket, to_frame(silver(8)),
                                {"model_version": "7"}, self.prefix)
        frame, info = baseline.load_baseline(self.fs, self.baseline_bucket, self.prefix)
        self.assertEqual((len(frame), info["source"], info["model_version"], info["rows"]), (8, "current", "7", 8))
        self.assertEqual(list(frame.columns), baseline.COLUMNS)

    def test_refresh_is_idempotent_and_follows_the_production_version(self) -> None:
        self.seed_train()
        table = FakeTable({101: silver(10), 102: silver(14)}, current=102)
        client = FakeClient()

        no_model = refresh(client, self.fs, self.baseline_bucket, table, self.prefix)
        self.assertFalse(no_model["refreshed"])

        client.serve("7", snapshot_id=101)
        first = refresh(client, self.fs, self.baseline_bucket, table, self.prefix)
        self.assertTrue(first["refreshed"])
        self.assertEqual((first["rows"], first["source"], first["silver_snapshot_id"]), (10, "silver_snapshot", 101))

        again = refresh(client, self.fs, self.baseline_bucket, table, self.prefix)
        self.assertFalse(again["refreshed"])  # same version → nothing rewritten
        self.assertEqual(baseline.read_meta(self.fs, self.baseline_bucket, self.prefix)["created_at"],
                         first["created_at"])

        client.serve("8", snapshot_id=102)
        moved = refresh(client, self.fs, self.baseline_bucket, table, self.prefix)
        self.assertEqual((moved["refreshed"], moved["rows"], moved["model_version"]), (True, 14, "8"))

    def test_expired_snapshot_falls_back_to_current_silver(self) -> None:
        table = FakeTable({102: silver(14)}, current=102)
        client = FakeClient()
        client.serve("9", snapshot_id=1)  # expired
        expired = refresh(client, self.fs, self.baseline_bucket, table, self.prefix)
        self.assertEqual((expired["source"], expired["rows"]), ("silver_current", 14))

    def test_version_without_snapshot_lineage_leaves_the_baseline_alone(self) -> None:
        self.seed_train(5)
        table = FakeTable({102: silver(14)}, current=102)
        client = FakeClient()
        client.serve("3", snapshot_id=None)  # trained before the param existed
        old = refresh(client, self.fs, self.baseline_bucket, table, self.prefix)
        self.assertFalse(old["refreshed"])
        self.assertIsNone(baseline.read_meta(self.fs, self.baseline_bucket, self.prefix))
        frame, info = baseline.load_baseline(self.fs, self.baseline_bucket, self.prefix)
        self.assertEqual((len(frame), info["source"]), (5, "train.parquet"))

    def test_latest_drift_summary_picks_the_newest_report(self) -> None:
        self.assertIsNone(latest_drift_summary(self.fs, self.reports_bucket, self.job))
        for stamp, drift in (("20260101T000000Z", True), ("20260102T000000Z", False)):
            with self.fs.open(f"{self.reports_bucket}/{self.job}/{stamp}.json", "w") as file:
                json.dump({"metrics": [], "reco_summary": {"dataset_drift": drift, "current_rows": 3}}, file)
        summary = latest_drift_summary(self.fs, self.reports_bucket, self.job)
        self.assertFalse(summary["dataset_drift"])
        self.assertTrue(summary["report_path"].endswith("20260102T000000Z.json"))


def to_frame(rows: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"parent_asin": rows["product_id"], "rating": rows["rating"]})


if __name__ == "__main__":
    unittest.main()
