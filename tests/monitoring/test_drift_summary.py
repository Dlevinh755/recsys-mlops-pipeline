"""ADR 0014 — `extract_drift_summary` against a real Evidently run (no I/O)."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
from evidently.metric_preset import DataDriftPreset
from evidently.report import Report

from jobs.monitoring.data_drift_report import extract_drift_summary


def run(reference: pd.DataFrame, current: pd.DataFrame) -> dict:
    report = Report(metrics=[DataDriftPreset()])
    report.run(reference_data=reference, current_data=current)
    return report.as_dict()


class ExtractDriftSummaryTest(unittest.TestCase):
    def test_same_distribution_is_not_drift(self) -> None:
        rng = np.random.default_rng(0)
        frame = pd.DataFrame({"rating": rng.choice([1.0, 3.0, 5.0], 2000), "is_cold_start_item": False})
        summary = extract_drift_summary(run(frame, frame.copy()))
        self.assertFalse(summary["dataset_drift"])
        self.assertEqual(summary["drifted_columns"], [])
        self.assertEqual(set(summary["column_scores"]), {"rating", "is_cold_start_item"})

    def test_shifted_columns_are_named(self) -> None:
        rng = np.random.default_rng(0)
        reference = pd.DataFrame({"rating": rng.choice([3.0, 4.0, 5.0], 2000), "is_cold_start_item": False})
        current = pd.DataFrame({"rating": rng.choice([1.0, 2.0], 2000),
                                "is_cold_start_item": rng.random(2000) < 0.6})
        summary = extract_drift_summary(run(reference, current))
        self.assertTrue(summary["dataset_drift"])
        self.assertEqual(sorted(summary["drifted_columns"]), ["is_cold_start_item", "rating"])
        self.assertEqual(summary["share_of_drifted_columns"], 1.0)

    def test_rejects_a_report_without_the_dataset_verdict(self) -> None:
        with self.assertRaises(RuntimeError):
            extract_drift_summary({"metrics": []})


if __name__ == "__main__":
    unittest.main()
