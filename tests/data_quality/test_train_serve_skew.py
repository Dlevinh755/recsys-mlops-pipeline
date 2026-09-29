"""Phase 8 — train/serve skew: for the same users, the sequence
`jobs/training/build_sequence_dataset.py` would train on (Gold/Iceberg,
offline) must match what `serving` actually scores against at request time
(Feast/Redis, online) — see `serving/app/core/sequence_source.py`'s own
docstring on why Hướng B needs the two to agree.

Real infra, no mock (same policy as `tests/integration/test_iceberg_writer.py`):
runs inside `materialize-job` (the one image with both `pyiceberg` and
`feast`), against a live Lakekeeper + Redis + `feature_repo` already
materialized (`jobs/materialize/feast_materialize.py` must have run at
least once — CI's `Integration Test` stage does this before running these
tests, see Jenkinsfile).
"""

from __future__ import annotations

import unittest

from feast import FeatureStore

from jobs.transform.iceberg_writer import get_or_create_table
from reco_mlops_libs.iceberg.catalog import get_catalog
from reco_mlops_libs.iceberg.schema_contracts import gold_user_features

FEATURE_REPO_PATH = "feature_repo"
SAMPLE_SIZE = 20  # cheap at this data scale; no need to check every user


def offline_sequences() -> dict[str, list[str]]:
    catalog = get_catalog()
    table = get_or_create_table(catalog, gold_user_features.TABLE_IDENTIFIER, gold_user_features.ICEBERG_SCHEMA)
    rows = table.scan(selected_fields=("user_id", "item_sequence")).to_arrow().to_pylist()
    return {row["user_id"]: list(row["item_sequence"]) for row in rows[:SAMPLE_SIZE]}


def online_sequence(store: FeatureStore, user_id: str) -> list[str]:
    result = store.get_online_features(
        features=["user_recent_items:item_sequence"], entity_rows=[{"user_id": user_id}]
    ).to_dict()
    sequences = result.get("item_sequence") or [[]]
    return list(sequences[0] or [])


class TrainServeSkewTest(unittest.TestCase):
    def test_online_sequence_matches_offline_gold_for_every_sampled_user(self) -> None:
        offline = offline_sequences()
        self.assertGreater(len(offline), 0, "gold.user_features is empty — run Phase 2/3 jobs first")

        store = FeatureStore(repo_path=FEATURE_REPO_PATH)
        mismatches = {}
        for user_id, offline_seq in offline.items():
            online_seq = online_sequence(store, user_id)
            if online_seq != offline_seq:
                mismatches[user_id] = {"offline": offline_seq, "online": online_seq}

        self.assertEqual(
            mismatches, {}, f"{len(mismatches)}/{len(offline)} sampled users have offline/online skew"
        )


if __name__ == "__main__":
    unittest.main()
