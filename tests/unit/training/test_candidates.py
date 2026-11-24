"""ADR 0015 — candidate list parsing and the winner rule (pure, no training)."""

from __future__ import annotations

import unittest

import numpy as np

from jobs.training.candidates import Scored, parse_candidates, select_winner

N = 1500


def base_scores() -> np.ndarray:
    rng = np.random.default_rng(0)
    return rng.choice([0.0, 0.0, 0.0, 0.3, 0.5, 1.0], N)


def scored(candidate_id: str, per_sample: np.ndarray, n_params: int) -> Scored:
    return Scored(candidate_id, float(per_sample.mean()), per_sample, n_params)


def shifted(base: np.ndarray, shift: float, seed: int) -> np.ndarray:
    """`base` + exactly `shift` on average, with zero-mean per-sample noise (SD 0.2, so the
    paired SE is about 0.005 and a shift of 0.003 is ~0.6 SE, 0.03 is ~6 SE)."""
    noise = np.random.default_rng(seed).normal(0.0, 0.2, N)
    return base + shift + (noise - noise.mean())


class SelectWinnerTest(unittest.TestCase):
    def test_default_wins_when_the_others_are_worse(self) -> None:
        base = base_scores()
        result = select_winner([scored("gru4rec", base, 100),
                                scored("sasrec", shifted(base, -0.05, 1), 150),
                                scored("gru4rec_sum", shifted(base, -0.02, 2), 90)])
        self.assertEqual(result.winner, "gru4rec")
        self.assertFalse(any(m["eligible"] for m in result.margins.values()))

    def test_higher_but_within_one_standard_error_keeps_the_default(self) -> None:
        base = base_scores()
        candidate = shifted(base, 0.003, 3)
        margin = select_winner([scored("gru4rec", base, 100), scored("sasrec", candidate, 150)]).margins["sasrec"]
        self.assertGreater(margin["diff"], 0)  # the fixture really is "higher but not clearly"
        self.assertLessEqual(margin["z"], 1.0)
        self.assertEqual(select_winner([scored("gru4rec", base, 100), scored("sasrec", candidate, 150)]).winner,
                         "gru4rec")

    def test_beyond_one_standard_error_wins(self) -> None:
        base = base_scores()
        result = select_winner([scored("gru4rec", base, 100), scored("sasrec", shifted(base, 0.03, 4), 150)])
        self.assertEqual(result.winner, "sasrec")
        self.assertGreater(result.margins["sasrec"]["z"], 1.0)

    def test_several_eligible_picks_the_higher_validation_score(self) -> None:
        base = base_scores()
        result = select_winner([scored("gru4rec", base, 100),
                                scored("sasrec", shifted(base, 0.03, 5), 150),
                                scored("gru4rec_sum", shifted(base, 0.06, 6), 90)])
        self.assertEqual(result.winner, "gru4rec_sum")
        self.assertTrue(all(m["eligible"] for m in result.margins.values()))

    def test_tie_between_eligible_goes_to_fewer_parameters(self) -> None:
        base = base_scores()
        better = shifted(base, 0.05, 7)
        result = select_winner([scored("gru4rec", base, 100), scored("sasrec", better, 150),
                                scored("gru4rec_sum", better.copy(), 90)])
        self.assertEqual(result.winner, "gru4rec_sum")

    def test_single_candidate_wins(self) -> None:
        result = select_winner([scored("sasrec", base_scores(), 150)])
        self.assertEqual((result.winner, result.default, result.margins), ("sasrec", "sasrec", {}))

    def test_default_is_the_first_candidate_when_gru4rec_is_absent(self) -> None:
        base = base_scores()
        result = select_winner([scored("sasrec", base, 150), scored("gru4rec_sum", shifted(base, 0.05, 8), 90)])
        self.assertEqual((result.default, result.winner), ("sasrec", "gru4rec_sum"))

    def test_rejects_candidates_scored_on_different_samples(self) -> None:
        with self.assertRaises(ValueError):
            select_winner([scored("gru4rec", np.zeros(5), 1), scored("sasrec", np.zeros(6), 1)])

    def test_identical_candidates_keep_the_default(self) -> None:
        base = base_scores()
        result = select_winner([scored("gru4rec", base, 100), scored("sasrec", base.copy(), 150)])
        self.assertEqual(result.winner, "gru4rec")  # diff == 0 → not eligible


class ParseCandidatesTest(unittest.TestCase):
    def test_default_and_list(self) -> None:
        self.assertEqual(parse_candidates(None), ["gru4rec"])
        self.assertEqual(parse_candidates(""), ["gru4rec"])
        self.assertEqual(parse_candidates("gru4rec, sasrec"), ["gru4rec", "sasrec"])

    def test_unknown_and_duplicate_are_errors(self) -> None:
        with self.assertRaises(ValueError):
            parse_candidates("gru4rec,bert4rec")
        with self.assertRaises(ValueError):
            parse_candidates("sasrec,sasrec")


if __name__ == "__main__":
    unittest.main()
