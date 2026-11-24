"""ADR 0013 — champion scored on the challenger's split
(`jobs/training/champion_eval.py`), with a stub net so the vocabulary
translation is checked by hand."""

from __future__ import annotations

import math
import unittest

import numpy as np
import torch

from jobs.training.champion_eval import Champion, champion_ranks, evaluate_champion, ranks_to_metrics
from reco_mlops_libs.ranking.agf.trainer import Samples


class FirstItemNet(torch.nn.Module):
    """`seq_output` = table row of the first history item (deterministic)."""

    max_len = 3

    def seq_output(self, seq, lengths, table):
        return table[seq[:, 0]]


def make_champion() -> Champion:
    table = torch.tensor([[0.0, 0.0], [1.0, 0.0], [0.5, 0.5], [0.2, 0.0]])  # PAD, a, b, c
    return Champion("4", FirstItemNet(), {"a": 1, "b": 2, "c": 3}, table)


# Challenger vocab: x is unknown to the champion, f0..f24 are filler (also
# unknown, score 0.0) so the catalog is larger than the top-20 window.
TOKENS = ["[PAD]", "x", "a", "b", "c", *[f"f{i}" for i in range(25)]]
X, A, B, C = 1, 2, 3, 4


def make_samples() -> Samples:
    seq = np.zeros((4, 3), dtype=np.int64)
    seq[0, :1] = [A]
    seq[1, :2] = [X, A]  # x is dropped → history [a]
    seq[2, :1] = [A]
    seq[3, :1] = [X]  # nothing left after dropping x
    return Samples(seq, np.array([1, 2, 1, 1]), np.array([B, C, X, B]), np.arange(4))


class ChampionRanksTest(unittest.TestCase):
    def test_translates_vocab_and_counts_what_it_cannot_score(self) -> None:
        ranks, info = champion_ranks(make_champion(), make_samples(), TOKENS)
        # sample 0: a=1.0 > b=0.5 → b is 2nd
        # sample 1: x dropped, state of a; a=1.0, b=0.5, c=0.2 → c is 3rd
        # sample 2: target x unknown to the champion → miss (21)
        # sample 3: no usable history → miss (21)
        np.testing.assert_array_equal(ranks, [2, 3, 21, 21])
        self.assertEqual(info, {"samples": 4, "unknown_target": 1, "no_history": 1,
                                "dropped_history_items": 2})

    def test_evaluate_champion_names_metrics_and_params(self) -> None:
        samples = make_samples()
        metrics, params = evaluate_champion(make_champion(), {"valid": samples, "test": samples}, TOKENS)
        expected = (1 / math.log2(3) + 1 / math.log2(4)) / 4  # ranks 2 and 3 hit, two misses
        self.assertAlmostEqual(metrics["champion_val_ndcg_at_10_same_split"], expected)
        self.assertAlmostEqual(metrics["champion_test_ndcg_at_10_same_split"], expected)
        self.assertAlmostEqual(metrics["champion_val_recall_at_10_same_split"], 0.5)
        self.assertEqual(
            set(metrics),
            {f"champion_{s}_{m}_at_{k}_same_split" for s in ("val", "test") for m in ("ndcg", "recall")
             for k in (10, 20)},
        )
        self.assertEqual(params["champion_version"], "4")
        self.assertEqual(params["champion_val_unknown_target"], 1)
        self.assertEqual(params["champion_test_no_history"], 1)


class RanksToMetricsTest(unittest.TestCase):
    def test_matches_hand_computed_values(self) -> None:
        out = ranks_to_metrics(np.array([1, 2, 21]))
        self.assertAlmostEqual(out["ndcg@10"], (1.0 + 1 / math.log2(3)) / 3)
        self.assertAlmostEqual(out["recall@10"], 2 / 3)
        self.assertAlmostEqual(out["recall@20"], 2 / 3)

    def test_empty_is_zero(self) -> None:
        self.assertEqual(ranks_to_metrics(np.array([]))["ndcg@10"], 0.0)


if __name__ == "__main__":
    unittest.main()
