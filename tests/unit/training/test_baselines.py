"""Phase 10 (ADR 0008) — popularity baseline (`baselines.py`), pure numpy."""

from __future__ import annotations

import math
import unittest

import numpy as np

from jobs.training.baselines import popularity_metrics, popularity_ranks


class PopularityBaselineTest(unittest.TestCase):
    def test_ranks_most_popular_first_pad_last_ties_by_index(self) -> None:
        counts = np.array([99, 3, 5, 3, 0])  # index 0 = [PAD], ignored
        np.testing.assert_array_equal(popularity_ranks(counts), [5, 2, 1, 3, 4])

    def test_metrics_match_hand_computed_values(self) -> None:
        counts = np.array([0, 3, 5, 3, 0])
        targets = np.array([2, 1, 4])  # ranks 1, 2, 4
        out = popularity_metrics(targets, counts)
        expected_ndcg = (1 / math.log2(2) + 1 / math.log2(3) + 1 / math.log2(5)) / 3
        self.assertAlmostEqual(out["ndcg@10"], expected_ndcg)
        self.assertAlmostEqual(out["recall@10"], 1.0)
        self.assertEqual(set(out), {"ndcg@10", "recall@10", "ndcg@20", "recall@20"})

    def test_target_beyond_k_scores_zero(self) -> None:
        counts = np.r_[0, np.arange(30, 0, -1)]  # item 30 has rank 30
        out = popularity_metrics(np.array([30]), counts)
        self.assertEqual(out["ndcg@20"], 0.0)
        self.assertEqual(out["recall@10"], 0.0)


if __name__ == "__main__":
    unittest.main()
