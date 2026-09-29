from __future__ import annotations

import math
import unittest

from jobs.training.evaluate import coverage_at_k, map_at_k, ndcg_at_k, recall_at_k


class RankMetricsTest(unittest.TestCase):
    def test_target_ranked_first_gets_max_ndcg_and_hits(self) -> None:
        scores = [10.0, 1.0, 0.0]  # index 0 has the highest score -> rank 1
        self.assertAlmostEqual(ndcg_at_k(scores, target_index=0, k=10), 1.0 / math.log2(2))
        self.assertEqual(recall_at_k(scores, target_index=0, k=10), 1.0)
        self.assertEqual(map_at_k(scores, target_index=0, k=10), 1.0)

    def test_target_ranked_second_matches_hand_computed_values(self) -> None:
        scores = [1.0, 2.0, 0.0]  # index1 highest, index0 second -> rank 2
        rank = 2
        self.assertAlmostEqual(ndcg_at_k(scores, target_index=0, k=10), 1.0 / math.log2(rank + 1))
        self.assertEqual(recall_at_k(scores, target_index=0, k=10), 1.0)
        self.assertAlmostEqual(map_at_k(scores, target_index=0, k=10), 1.0 / rank)

    def test_target_outside_k_scores_zero(self) -> None:
        scores = [5.0, 4.0, 3.0, 2.0, 1.0]  # index 4 is last -> rank 5
        self.assertEqual(ndcg_at_k(scores, target_index=4, k=2), 0.0)
        self.assertEqual(recall_at_k(scores, target_index=4, k=2), 0.0)
        self.assertEqual(map_at_k(scores, target_index=4, k=2), 0.0)

    def test_coverage_counts_distinct_recommended_items(self) -> None:
        top_k = [[1, 2, 3], [2, 3, 4]]
        self.assertAlmostEqual(coverage_at_k(top_k, vocab_size=10, k=3), 4 / 10)

    def test_coverage_with_zero_vocab_size_is_zero(self) -> None:
        self.assertEqual(coverage_at_k([], vocab_size=0, k=3), 0.0)


if __name__ == "__main__":
    unittest.main()
