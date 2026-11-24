"""ADR 0014 — `retrain_decision.decide` (pure) and the drift summary parser."""

from __future__ import annotations

import unittest

from jobs.monitoring.retrain_decision import Policy, Signals, decide

POLICY = Policy(min_new_rows=200, new_rows_ratio=0.20, max_model_age_days=14, cooldown_hours=24)


def signals(**overrides) -> Signals:
    base = dict(
        dataset_drift=False,
        drifted_columns=[],
        current_rows=10_100,
        reference_rows=10_000,  # 100 new rows = 1%
        reference_source="latest_version",
        hours_since_last_train=72.0,
        production_age_days=3.0,
    )
    base.update(overrides)
    return Signals(**base)


class DecideTest(unittest.TestCase):
    def test_nothing_new_skips(self) -> None:
        decision = decide(signals(current_rows=10_000), POLICY)
        self.assertFalse(decision.retrain)
        self.assertIn("nothing to do", decision.reasons[0])

    def test_drift_with_enough_rows_retrains(self) -> None:
        decision = decide(signals(dataset_drift=True, drifted_columns=["is_cold_start_item"],
                                  current_rows=10_300), POLICY)  # 300 new rows, 3%
        self.assertTrue(decision.retrain)
        self.assertEqual(len(decision.reasons), 1)
        self.assertIn("is_cold_start_item", decision.reasons[0])

    def test_drift_with_too_few_rows_skips(self) -> None:
        decision = decide(signals(dataset_drift=True, drifted_columns=["rating"], current_rows=10_150), POLICY)
        self.assertFalse(decision.retrain)
        self.assertIn("only 150 new rows", decision.reasons[0])

    def test_no_drift_but_enough_row_ratio_retrains(self) -> None:
        decision = decide(signals(current_rows=12_500), POLICY)  # 25%
        self.assertTrue(decision.retrain)
        self.assertIn("25.0%", decision.reasons[0])

    def test_old_model_retrains(self) -> None:
        decision = decide(signals(production_age_days=20.0), POLICY)
        self.assertTrue(decision.retrain)
        self.assertIn("model age", decision.reasons[0])

    def test_cooldown_blocks_everything(self) -> None:
        decision = decide(signals(dataset_drift=True, current_rows=20_000, production_age_days=30,
                                  hours_since_last_train=2.0), POLICY)
        self.assertFalse(decision.retrain)
        self.assertIn("cooldown", decision.reasons[0])

    def test_zero_cooldown_never_blocks(self) -> None:
        decision = decide(signals(current_rows=12_500, hours_since_last_train=0.01),
                          Policy(cooldown_hours=0))
        self.assertTrue(decision.retrain)

    def test_every_matching_rule_is_listed(self) -> None:
        decision = decide(signals(dataset_drift=True, drifted_columns=["rating"], current_rows=13_000,
                                  production_age_days=20), POLICY)
        self.assertEqual(len(decision.reasons), 3)

    def test_fallback_reference_is_reported(self) -> None:
        decision = decide(signals(reference_source="drift_baseline", reference_rows=14_748,
                                  current_rows=18_456), POLICY)  # the real 2026-11-24 numbers: 25.1%
        self.assertTrue(decision.retrain)
        self.assertEqual(decision.new_rows, 3_708)
        self.assertEqual(decision.as_dict()["signals"]["reference_source"], "drift_baseline")

    def test_no_production_model_retrains(self) -> None:
        decision = decide(signals(production_age_days=None, hours_since_last_train=None), POLICY)
        self.assertTrue(decision.retrain)
        self.assertIn("no production", decision.reasons[0])

    def test_missing_drift_report_is_noted_not_fatal(self) -> None:
        decision = decide(signals(dataset_drift=None), POLICY)
        self.assertFalse(decision.retrain)
        self.assertIn("no drift report", decision.reasons[0])

    def test_decision_is_json_ready(self) -> None:
        import json

        json.dumps(decide(signals(), POLICY).as_dict())


if __name__ == "__main__":
    unittest.main()
