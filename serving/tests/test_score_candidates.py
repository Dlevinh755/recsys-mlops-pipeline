"""ADR 0008 — `model_loader.score_candidates` (serving path) must give the
same scores as the model's own full-ranking path used in training
(`AGFSequentialModel.score_with_tables` with the precomputed `H`). Runs in
the `serving` image (torch), no MLflow/Redis/Feast needed."""

from __future__ import annotations

import unittest

import numpy as np
import torch

from reco_mlops_libs.ranking.agf.agf_core import AGFSequentialModel
from serving.app.core.model_loader import load_item_table, score_candidates


# ADR 0015: the trainer may now pick a non-default candidate (backbone/fusion);
# serving scores every candidate through the same `net.seq_output` + `H` path.
VARIANTS = {
    "gru4rec": {"backbone": "gru4rec"},
    "sasrec": {"backbone": "sasrec"},
    "gru4rec_sum": {"backbone": "gru4rec", "fusion": "sum"},
    "gru4rec_idonly": {"backbone": "gru4rec", "fusion": "id_only"},
}


def _tiny_model(n_items: int = 12, max_len: int = 4, **overrides) -> AGFSequentialModel:
    torch.manual_seed(0)
    options = {"backbone": "gru4rec", **overrides}
    model = AGFSequentialModel(n_items, n_categories=4, n_stores=3, n_prices=3,
                               d=8, max_len=max_len, dropout=0.0, use_text=False, **options)
    rng = np.random.default_rng(0)
    counts = rng.integers(0, 5, n_items)
    counts[0] = 0
    model.set_item_features(rng.integers(1, 4, (n_items, 2)), rng.integers(1, 3, n_items),
                            rng.integers(1, 3, n_items), None, counts)
    return model.eval()


class ScoreCandidatesTest(unittest.TestCase):
    def test_matches_full_ranking_path_and_unknown_items_score_zero(self) -> None:
        model = _tiny_model()
        with torch.no_grad():
            table = model.item_tables()[0]
        history = [3, 5, 7]
        candidates = [1, None, 7, 11]
        served = score_candidates(model, table, history, candidates)
        with torch.no_grad():
            full = model.score_with_tables(torch.tensor([history]), torch.tensor([3]), table, table)[0]
        self.assertEqual(served[1], 0.0)
        for got, index in zip(served, candidates):
            if index is not None:
                self.assertAlmostEqual(got, float(full[index]), places=5)

    def test_every_trainer_candidate_scores_like_its_full_ranking_path(self) -> None:
        for name, options in VARIANTS.items():
            with self.subTest(candidate=name):
                model = _tiny_model(**options)
                with torch.no_grad():
                    table = model.item_tables()[0]
                history = [3, 5, 7]
                candidates = [1, None, 7, 11]
                served = score_candidates(model, table, history, candidates)
                with torch.no_grad():
                    state = model.seq_output(torch.tensor([history]), torch.tensor([3]), table)[0]
                    full = model.score_with_tables(torch.tensor([history]), torch.tensor([3]), table, table)[0]
                self.assertEqual(served[1], 0.0)
                for got, index in zip(served, candidates):
                    if index is not None:
                        self.assertAlmostEqual(got, float(table[index] @ state), places=5)  # serving: H · state
                        self.assertAlmostEqual(got, float(full[index]), places=5)  # training: full ranking

    def test_sasrec_uses_only_the_latest_max_len_items(self) -> None:
        model = _tiny_model(max_len=2, backbone="sasrec")
        with torch.no_grad():
            table = model.item_tables()[0]
        self.assertEqual(score_candidates(model, table, [9, 4, 3, 5], [2, 6]),
                         score_candidates(model, table, [3, 5], [2, 6]))

    def test_only_latest_max_len_items_are_used(self) -> None:
        model = _tiny_model(max_len=2)
        with torch.no_grad():
            table = model.item_tables()[0]
        self.assertEqual(score_candidates(model, table, [9, 4, 3, 5], [2, 6]),
                         score_candidates(model, table, [3, 5], [2, 6]))

    def test_item_table_row_count_is_checked(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.npy"
            np.save(path, np.zeros((3, 8), dtype=np.float32))
            self.assertEqual(load_item_table(str(path), {"a": 1, "b": 2}).shape, (3, 8))
            with self.assertRaises(ValueError):
                load_item_table(str(path), {"a": 1})


if __name__ == "__main__":
    unittest.main()
