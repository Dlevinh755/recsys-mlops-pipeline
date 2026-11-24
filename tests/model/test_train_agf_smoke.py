"""Phase 10 (ADR 0008) — model smoke test for AGF-GRU4Rec: the real
`train_agf.run_training` (mapping → `trainer.train` → export → MLflow log)
on the same fixed-seed fixture as `test_training_smoke.py`
(`tests/model/sample_data/`), with deterministic synthetic attributes, no
text branch (no network for MiniLM in CI) and a `file://` tracking URI — no
Iceberg/MLflow server needed. Runs inside the `training` image (torch)."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

SAMPLE_DATA_DIR = Path(__file__).resolve().parent / "sample_data"
CATEGORIES = ("Cases", "Chargers", "Screen Protectors")


def load_sample_rows() -> tuple[list[dict], list[dict]]:
    items = json.loads((SAMPLE_DATA_DIR / "item_catalog.json").read_text())
    sequences = json.loads((SAMPLE_DATA_DIR / "user_sequences.json").read_text())
    item_rows = [
        {
            "product_id": pid,
            "title": f"Sample product {k}",
            "category": "Cell Phones & Accessories",
            "category_path": f"Cell Phones & Accessories > Accessories > {CATEGORIES[k % 3]}",
            "brand": f"brand_{k % 6}",
            "price": 5.0 + k,
        }
        for k, pid in enumerate(items)
    ]
    return item_rows, sequences


class AGFModelSmokeTest(unittest.TestCase):
    def test_train_agf_runs_end_to_end_on_sample_data(self) -> None:
        import mlflow

        from jobs.training.train_agf import CONFIG_PATH, run_training
        from reco_mlops_libs.ranking.agf.trainer import MethodConfig

        mlflow.set_tracking_uri(f"file://{tempfile.mkdtemp(prefix='mlops4rec-agf-smoke-')}")
        cfg = replace(MethodConfig.from_yaml(CONFIG_PATH), use_text=False, epochs=2, batch_size=64)
        item_rows, user_rows = load_sample_rows()

        result = run_training(item_rows, user_rows, cfg, seed=2023, register=False)

        for key in ("val_ndcg_at_10", "val_recall_at_10", "test_ndcg_at_10", "baseline_pop_val_ndcg_at_10"):
            self.assertIn(key, result)
            self.assertIsInstance(result[key], float)
        self.assertLess(result["item_table_max_abs_diff"], 1e-5)

        client = mlflow.MlflowClient()
        run = client.get_run(result["run_id"])
        self.assertEqual(run.info.status, "FINISHED")
        artifacts = {a.path for a in client.list_artifacts(result["run_id"], "agf")}
        self.assertIn("agf/item_table.npy", artifacts)
        # Run facts are params (not one-bar metrics); curves are per-epoch; figures logged.
        for key in ("best_epoch", "epochs_run", "train_seconds", "train_samples", "val_samples", "n_params"):
            self.assertIn(key, run.data.params)
            self.assertNotIn(key, run.data.metrics)
        for key in ("train_loss", "val_ndcg_at_10_epoch", "val_recall_at_10_epoch",
                    "baseline_pop_val_ndcg_at_10_epoch", "baseline_pop_val_recall_at_10_epoch"):
            steps = {m.step for m in client.get_metric_history(result["run_id"], key)}
            self.assertEqual(steps, set(range(result["epochs_run"])), key)
        figures = {a.path for a in client.list_artifacts(result["run_id"], "figures")}
        self.assertEqual(figures, {"figures/training_curves.png", "figures/agf_vs_popularity.png"})
        vocab_path = client.download_artifacts(result["run_id"], "item_vocab.json")
        vocab = json.loads(Path(vocab_path).read_text())
        self.assertEqual(len(vocab), len(item_rows))
        self.assertEqual(min(vocab.values()), 1)
        # No champion given → no same-split numbers in the run.
        self.assertFalse([key for key in run.data.metrics if key.startswith("champion_")])
        self.assertNotIn("champion_version", run.data.params)

    def test_second_run_scores_the_champion_on_its_own_split(self) -> None:
        """ADR 0013: with a champion, the run logs the champion's score on the
        challenger's valid/test samples. Same data and seed → same split, so the
        champion path (serving-style scoring from the exported item table) must
        reproduce the champion's own `val_ndcg_at_10` from `trainer.evaluate`."""
        import mlflow

        from jobs.training.champion_eval import load_champion
        from jobs.training.train_agf import CONFIG_PATH, run_training
        from reco_mlops_libs.ranking.agf.trainer import MethodConfig

        mlflow.set_tracking_uri(f"file://{tempfile.mkdtemp(prefix='mlops4rec-agf-champion-')}")
        cfg = replace(MethodConfig.from_yaml(CONFIG_PATH), use_text=False, epochs=2, batch_size=64)
        item_rows, user_rows = load_sample_rows()

        first = run_training(item_rows, user_rows, cfg, seed=2023, register=False)
        champion = load_champion(f"runs:/{first['run_id']}/model", first["run_id"], "1")
        second = run_training(item_rows, user_rows, cfg, seed=2023, register=False, champion=champion)

        run = mlflow.MlflowClient().get_run(second["run_id"])
        self.assertEqual(run.data.params["champion_version"], "1")
        for split in ("val", "test"):
            for metric in ("ndcg", "recall"):
                for k in (10, 20):
                    self.assertIn(f"champion_{split}_{metric}_at_{k}_same_split", run.data.metrics)
            self.assertEqual(run.data.params[f"champion_{split}_unknown_target"], "0")
            self.assertEqual(run.data.params[f"champion_{split}_no_history"], "0")
        self.assertAlmostEqual(
            run.data.metrics["champion_val_ndcg_at_10_same_split"], first["val_ndcg_at_10"], places=4)
        self.assertAlmostEqual(
            run.data.metrics["champion_test_ndcg_at_10_same_split"], first["test_ndcg_at_10"], places=4)


if __name__ == "__main__":
    unittest.main()
