"""ADR 0013 — promotion decision (`promote.decide_promotion`), pure function."""

from __future__ import annotations

import unittest

from jobs.training.promote import FIRST, LEGACY, SAME_SPLIT, decide_promotion, lift_over_pop


def decide(**overrides):
    args = dict(
        candidate_metric=0.0847,
        production_version="4",
        production_metric=0.0920,
        champion_version="4",
        champion_metric_same_split=0.0800,
        min_delta=0.0,
    )
    args.update(overrides)
    return decide_promotion(**args)


class DecidePromotionTest(unittest.TestCase):
    def test_raw_lower_but_same_split_higher_promotes(self) -> None:
        # The real v5-vs-v4 case: 0.0847 < 0.0920 raw, but v4 scores 0.0800 on v5's split.
        decision = decide()
        self.assertTrue(decision.promote)
        self.assertEqual(decision.comparison, SAME_SPLIT)

    def test_same_split_lower_is_rejected(self) -> None:
        decision = decide(candidate_metric=0.0900, champion_metric_same_split=0.0950)
        self.assertFalse(decision.promote)
        self.assertEqual(decision.comparison, SAME_SPLIT)

    def test_same_split_raw_higher_but_not_better_is_rejected(self) -> None:
        # raw 0.0950 > production's own 0.0920 would pass the legacy rule
        decision = decide(candidate_metric=0.0950, champion_metric_same_split=0.0990)
        self.assertFalse(decision.promote)

    def test_tie_does_not_promote(self) -> None:
        decision = decide(candidate_metric=0.0800, champion_metric_same_split=0.0800)
        self.assertFalse(decision.promote)
        self.assertEqual(decision.comparison, SAME_SPLIT)

    def test_no_production_promotes(self) -> None:
        decision = decide(production_version=None, production_metric=None,
                          champion_version=None, champion_metric_same_split=None)
        self.assertTrue(decision.promote)
        self.assertEqual(decision.comparison, FIRST)

    def test_missing_same_split_falls_back_to_legacy_rule(self) -> None:
        worse = decide(champion_version=None, champion_metric_same_split=None)
        self.assertFalse(worse.promote)  # 0.0847 < 0.0920
        self.assertEqual(worse.comparison, LEGACY)
        equal = decide(candidate_metric=0.0920, champion_version=None, champion_metric_same_split=None)
        self.assertTrue(equal.promote)  # legacy keeps >=
        self.assertEqual(equal.comparison, LEGACY)

    def test_min_delta_requires_margin(self) -> None:
        near = decide(candidate_metric=0.0805, champion_metric_same_split=0.0800, min_delta=0.001)
        self.assertFalse(near.promote)
        clear = decide(candidate_metric=0.0815, champion_metric_same_split=0.0800, min_delta=0.001)
        self.assertTrue(clear.promote)

    def test_champion_scored_is_no_longer_production_falls_back(self) -> None:
        # candidate was scored against v3 but production moved to v4 meanwhile
        decision = decide(champion_version="3")
        self.assertEqual(decision.comparison, LEGACY)
        self.assertIn("v3", decision.reason)

    def test_legacy_without_production_metric_does_not_promote(self) -> None:
        decision = decide(production_metric=None, champion_version=None, champion_metric_same_split=None)
        self.assertFalse(decision.promote)
        self.assertEqual(decision.comparison, LEGACY)


class LiftOverPopTest(unittest.TestCase):
    def test_ratio_and_missing_values(self) -> None:
        self.assertAlmostEqual(
            lift_over_pop({"val_ndcg_at_10": 0.09, "baseline_pop_val_ndcg_at_10": 0.045}), 2.0)
        self.assertIsNone(lift_over_pop({"val_ndcg_at_10": 0.09}))
        self.assertIsNone(lift_over_pop({"val_ndcg_at_10": 0.09, "baseline_pop_val_ndcg_at_10": 0.0}))
        self.assertIsNone(lift_over_pop({}))


if __name__ == "__main__":
    unittest.main()
