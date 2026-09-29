from __future__ import annotations

import unittest

from serving.app.core.recency_boost import boost
from serving.app.core.sequence_source import merge_sequence, to_indices


class MergeSequenceTest(unittest.TestCase):
    def test_recent_items_appended_after_history(self) -> None:
        self.assertEqual(
            merge_sequence(historical_items=["a", "b"], recent_items=["c"]),
            ["a", "b", "c"],
        )

    def test_truncates_to_max_sequence_length(self) -> None:
        historical = [f"h{i}" for i in range(9)]
        result = merge_sequence(historical_items=historical, recent_items=["new1", "new2"])
        self.assertEqual(len(result), 10)
        # the 2 newest (session) items must survive truncation, oldest history drops first
        self.assertEqual(result[-2:], ["new1", "new2"])

    def test_empty_inputs_produce_empty_sequence(self) -> None:
        self.assertEqual(merge_sequence([], []), [])


class ToIndicesTest(unittest.TestCase):
    def test_maps_known_items_and_drops_unknown(self) -> None:
        vocab = {"a": 1, "b": 2}
        self.assertEqual(to_indices(["a", "x", "b"], vocab), [1, 2])

    def test_empty_sequence_yields_empty_indices(self) -> None:
        self.assertEqual(to_indices([], {"a": 1}), [])


class BoostTest(unittest.TestCase):
    def test_similar_items_to_most_recent_interaction_go_first(self) -> None:
        ranked = ["a", "b", "c", "d"]
        boosted = boost(ranked, recent_items=["x"], similar_items_by_id={"x": ["c"]})
        self.assertEqual(boosted, ["c", "a", "b", "d"])

    def test_most_recent_of_several_interactions_wins_the_front(self) -> None:
        ranked = ["a", "b", "c", "d"]
        boosted = boost(
            ranked,
            recent_items=["old_item", "new_item"],
            similar_items_by_id={"old_item": ["d"], "new_item": ["c"]},
        )
        self.assertEqual(boosted[0], "c")  # most recent interaction's similar item first

    def test_similar_item_outside_ranked_candidates_is_ignored(self) -> None:
        ranked = ["a", "b"]
        boosted = boost(ranked, recent_items=["x"], similar_items_by_id={"x": ["not_a_candidate"]})
        self.assertEqual(boosted, ["a", "b"])

    def test_no_recent_items_leaves_order_unchanged(self) -> None:
        ranked = ["a", "b", "c"]
        self.assertEqual(boost(ranked, recent_items=[], similar_items_by_id={}), ranked)


if __name__ == "__main__":
    unittest.main()


class BuildHistoryTest(unittest.TestCase):
    def test_newest_first_session_before_history(self) -> None:
        from serving.app.core.sequence_source import build_history

        result = build_history([("a", "t1"), ("b", "t2")], ["s1", "s2"])
        self.assertEqual([r["product_id"] for r in result], ["s2", "s1", "b", "a"])
        self.assertEqual([r["source"] for r in result], ["session", "session", "history", "history"])
        self.assertIsNone(result[0]["event_time"])
        self.assertEqual(result[2]["event_time"], "t2")

    def test_empty_inputs(self) -> None:
        from serving.app.core.sequence_source import build_history

        self.assertEqual(build_history([], []), [])
