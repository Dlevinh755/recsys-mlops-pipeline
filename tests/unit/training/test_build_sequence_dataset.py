from __future__ import annotations

import unittest

import pyarrow as pa

from jobs.training.build_sequence_dataset import build_dataset, build_item_vocab


class _FakeScan:
    def __init__(self, arrow_table: pa.Table) -> None:
        self._arrow_table = arrow_table

    def to_arrow(self) -> pa.Table:
        return self._arrow_table


class _FakeTable:
    """Stands in for a `pyiceberg.table.Table` — only `.scan().to_arrow()`
    is used by `build_sequence_dataset.py`, so that's all this fakes."""

    def __init__(self, arrow_table: pa.Table) -> None:
        self._arrow_table = arrow_table

    def scan(self, selected_fields=None) -> _FakeScan:
        return _FakeScan(self._arrow_table)


def _user_features_table(rows: list[dict]) -> _FakeTable:
    return _FakeTable(pa.Table.from_pylist(rows))


class BuildItemVocabTest(unittest.TestCase):
    def test_vocab_is_1_indexed_and_sorted(self) -> None:
        table = _FakeTable(pa.Table.from_pylist([{"product_id": "b"}, {"product_id": "a"}]))
        self.assertEqual(build_item_vocab(table), {"a": 1, "b": 2})


class BuildDatasetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.vocab = {"p1": 1, "p2": 2, "p3": 3}

    def test_user_with_single_item_is_skipped(self) -> None:
        table = _user_features_table([
            {"user_id": "u1", "item_sequence": ["p1"], "sequence_length": 1},
        ])
        dataset = build_dataset(table, self.vocab)
        self.assertEqual(dataset.skipped_users, 1)
        self.assertEqual(dataset.train_examples, ())
        self.assertEqual(dataset.val_examples, ())

    def test_user_with_two_items_contributes_val_only(self) -> None:
        table = _user_features_table([
            {"user_id": "u1", "item_sequence": ["p1", "p2"], "sequence_length": 2},
        ])
        dataset = build_dataset(table, self.vocab)
        self.assertEqual(dataset.skipped_users, 0)
        self.assertEqual(dataset.train_examples, ())
        self.assertEqual(len(dataset.val_examples), 1)
        self.assertEqual(dataset.val_examples[0].input_indices, (1,))
        self.assertEqual(dataset.val_examples[0].target_index, 2)

    def test_user_with_three_items_contributes_one_train_and_one_val(self) -> None:
        table = _user_features_table([
            {"user_id": "u1", "item_sequence": ["p1", "p2", "p3"], "sequence_length": 3},
        ])
        dataset = build_dataset(table, self.vocab)
        self.assertEqual(len(dataset.train_examples), 1)
        self.assertEqual(dataset.train_examples[0].input_indices, (1,))
        self.assertEqual(dataset.train_examples[0].target_index, 2)
        self.assertEqual(len(dataset.val_examples), 1)
        self.assertEqual(dataset.val_examples[0].input_indices, (1, 2))
        self.assertEqual(dataset.val_examples[0].target_index, 3)

    def test_user_with_ten_items_contributes_eight_train_examples(self) -> None:
        items = [f"p{i}" for i in range(1, 11)]
        vocab = {item: index for index, item in enumerate(items, start=1)}
        table = _user_features_table([
            {"user_id": "u1", "item_sequence": items, "sequence_length": 10},
        ])
        dataset = build_dataset(table, vocab)
        self.assertEqual(len(dataset.train_examples), 8)
        self.assertEqual(len(dataset.val_examples), 1)
        self.assertEqual(dataset.val_examples[0].target_index, vocab["p10"])

    def test_multiple_users_accumulate_independently(self) -> None:
        table = _user_features_table([
            {"user_id": "u1", "item_sequence": ["p1"], "sequence_length": 1},
            {"user_id": "u2", "item_sequence": ["p1", "p2", "p3"], "sequence_length": 3},
        ])
        dataset = build_dataset(table, self.vocab)
        self.assertEqual(dataset.skipped_users, 1)
        self.assertEqual(len(dataset.train_examples), 1)
        self.assertEqual(len(dataset.val_examples), 1)


if __name__ == "__main__":
    unittest.main()
