"""Phase 8 — `model-smoke-test` (ADR 0005 / de-xuat-trien-khai.md Phase 8).

Runs the *real* `build_sequence_dataset.build_item_vocab`/`build_dataset`
and `train_sequence.run_training` against a tiny fixed-seed fixture
(`tests/model/sample_data/`, generated once with `random.seed(42)` — see
that directory's own generation note) instead of live Iceberg/MLflow
server — no `docker compose up` needed (ADR 0005 update 2026-09-15):
`MLFLOW_TRACKING_URI=file://...` is enough for `mlflow.start_run()` to work
against a throwaway local directory.

`FakeTable` implements only the one `.scan(selected_fields=...).to_arrow()`
method `build_item_vocab`/`build_dataset` actually call — real production
code runs unmodified against it, this file supplies no logic of its own to
transform the fixture into a dataset.
"""

from __future__ import annotations

import argparse
import json
import unittest
from pathlib import Path

import pyarrow as pa

from jobs.training.build_sequence_dataset import build_dataset, build_item_vocab
from jobs.training.train_sequence import run_training

SAMPLE_DATA_DIR = Path(__file__).resolve().parent / "sample_data"


class FakeTable:
    """Stand-in for `pyiceberg.table.Table` — only the slice of its
    interface `build_item_vocab`/`build_dataset` call."""

    def __init__(self, rows: list[dict]) -> None:
        self._arrow = pa.Table.from_pylist(rows)

    def scan(self, selected_fields=None, **_ignored):
        arrow = self._arrow.select(list(selected_fields)) if selected_fields else self._arrow
        return _FakeScan(arrow)


class _FakeScan:
    def __init__(self, arrow: pa.Table) -> None:
        self._arrow = arrow

    def to_arrow(self) -> pa.Table:
        return self._arrow


def load_sample_dataset():
    items = json.loads((SAMPLE_DATA_DIR / "item_catalog.json").read_text())
    sequences = json.loads((SAMPLE_DATA_DIR / "user_sequences.json").read_text())

    item_features_table = FakeTable([{"product_id": item} for item in items])
    user_features_table = FakeTable(sequences)

    vocab = build_item_vocab(item_features_table)
    return build_dataset(user_features_table, vocab)


class ModelSmokeTest(unittest.TestCase):
    def test_train_sequence_runs_end_to_end_on_sample_data(self) -> None:
        import mlflow

        mlflow.set_tracking_uri(f"file://{self.mlruns_dir()}")
        dataset = load_sample_dataset()
        self.assertGreater(len(dataset.train_examples), 0)
        self.assertGreater(len(dataset.val_examples), 0)

        args = argparse.Namespace(
            epochs=1, batch_size=32, lr=1e-3, embedding_dim=8, hidden_size=16, num_negatives=10
        )
        result = run_training(dataset, args, user_features_table=None, register=False)

        self.assertIn("run_id", result)
        for metric in ("val_ndcg_at_10", "val_recall_at_10", "val_map_at_10", "val_coverage_at_10"):
            self.assertIn(metric, result)
            self.assertIsInstance(result[metric], float)

        client = mlflow.MlflowClient()
        run = client.get_run(result["run_id"])
        self.assertEqual(run.info.status, "FINISHED")

    def mlruns_dir(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="mlops4rec-model-smoke-")


if __name__ == "__main__":
    unittest.main()
