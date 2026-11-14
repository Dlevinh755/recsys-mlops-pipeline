"""Phase 10 (ADR 0008) — mapping Gold rows → AGF inputs (`agf_inputs.py`).
Pure pandas/numpy: no torch, Iceberg or MLflow needed."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np

from jobs.training.agf_inputs import (
    PAD,
    build_catalog_items,
    catalog_meta,
    interactions_from_sequences,
    split_category_path,
)
from reco_mlops_libs.ranking.agf import features as F

T0 = datetime(2023, 1, 1, tzinfo=timezone.utc)


def _item(pid, path=None, category="Cell Phones & Accessories", brand="acme", price=Decimal("9.99")):
    return {"product_id": pid, "title": f"Title {pid}", "category": category,
            "category_path": path, "brand": brand, "price": price}


def _user(uid, seq, with_times=True):
    row = {"user_id": uid, "item_sequence": seq}
    if with_times:
        row["event_time_sequence"] = [T0 + timedelta(days=i) for i in range(len(seq))]
    return row


class SplitCategoryPathTest(unittest.TestCase):
    def test_splits_and_strips_levels(self) -> None:
        self.assertEqual(split_category_path("A > B >  C "), ["A", "B", "C"])

    def test_empty_values_are_none(self) -> None:
        for value in (None, "", " > ", float("nan")):
            self.assertIsNone(split_category_path(value))


class CatalogMetaTest(unittest.TestCase):
    def test_columns_follow_the_features_contract(self) -> None:
        items, meta = catalog_meta([_item("b", "X > Y"), _item("a", None, brand=None, price=None)])
        self.assertEqual(items, ["a", "b"])  # sorted by product_id
        self.assertEqual(list(meta.columns), ["item_id", "title", "main_category", "categories", "store", "price"])
        row_b = meta.set_index("item_id").loc["b"]
        self.assertEqual(row_b["categories"], ["X", "Y"])
        self.assertEqual(row_b["store"], "acme")
        self.assertAlmostEqual(row_b["price"], 9.99)
        row_a = meta.set_index("item_id").loc["a"]
        self.assertIsNone(row_a["categories"])
        self.assertIsNone(row_a["store"])

    def test_duplicate_product_id_fails_loudly(self) -> None:
        with self.assertRaises(ValueError):
            catalog_meta([_item("a"), _item("a")])


class InteractionsFromSequencesTest(unittest.TestCase):
    def test_explodes_in_chronological_order(self) -> None:
        inter = interactions_from_sequences([_user("u2", ["c", "a"]), _user("u1", ["a", "b", "c"])])
        self.assertEqual(list(inter["user_id"]), ["u1", "u1", "u1", "u2", "u2"])
        self.assertEqual(list(inter["item_id"]), ["a", "b", "c", "c", "a"])
        self.assertTrue((inter.groupby("user_id")["timestamp"].diff().dropna() > 0).all())

    def test_positions_are_used_without_event_times(self) -> None:
        inter = interactions_from_sequences([_user("u1", ["b", "a"], with_times=False)])
        self.assertEqual(list(inter["item_id"]), ["b", "a"])
        self.assertEqual(list(inter["timestamp"]), [0.0, 1.0])


class BuildCatalogItemsTest(unittest.TestCase):
    def setUp(self) -> None:
        # 6 items share a 3-level path (≥ MIN_TOKEN_COUNT), "cold" never interacted.
        path = "Cell Phones & Accessories > Accessories > Screen Protectors"
        rows = [_item(f"i{k}", path, brand="acme" if k < 5 else "tiny") for k in range(6)]
        rows.append(_item("cold", None, category="Electronics", brand=None, price=None))
        self.items, self.meta = catalog_meta(rows)
        self.inter = interactions_from_sequences([
            _user("u1", ["i0", "i1", "i2", "i3"]),
            _user("u2", ["i1", "i2", "i4"]),
        ])

    def test_full_catalog_with_pad_row_and_train_only_counts(self) -> None:
        out = build_catalog_items(self.inter, self.meta, self.items, use_text=False)
        self.assertEqual(out.tokens[0], PAD)
        self.assertEqual(out.tokens[1:], self.items)
        self.assertIsNone(out.text)
        counts = dict(zip(out.tokens, out.train_counts))
        # leave-one-out: last 2 items of each user are valid/test, not counted
        self.assertEqual(counts["i0"], 1)
        self.assertEqual(counts["i1"], 2)
        self.assertEqual(counts["i2"], 0)
        self.assertEqual(counts["i3"], 0)
        self.assertEqual(counts["cold"], 0)
        self.assertEqual(counts[PAD], 0)
        self.assertEqual(out.vocab_index, {pid: i + 1 for i, pid in enumerate(self.items)})

    def test_attributes_follow_experiment_rules(self) -> None:
        out = build_catalog_items(self.inter, self.meta, self.items, use_text=False)
        feats, _ = F.build_item_features(self.meta, self.items)
        enc = F.encode_features(feats, F.fit_vocab(feats))
        np.testing.assert_array_equal(out.categories[1:], enc["categories"])
        np.testing.assert_array_equal(out.store[1:], enc["store"])
        np.testing.assert_array_equal(out.price[1:], enc["price"])
        self.assertTrue((out.categories[0] == 0).all())
        i0 = out.tokens.index("i0")
        self.assertEqual(int((out.categories[i0] > 0).sum()), 3)  # 3 category levels kept
        cold = feats.set_index("item_id").loc["cold"]
        self.assertEqual(cold["categories"], "[UNK]")  # main_category fallback, < 5 items
        self.assertEqual(cold["store"], F.MISSING)
        self.assertEqual(cold["price_bucket"], F.MISSING)
        self.assertEqual(feats.set_index("item_id").loc["i5", "store"], F.UNK)  # store < 5 items

    def test_text_uses_injected_encoder_in_catalog_order(self) -> None:
        seen = {}

        def fake_encode(texts):
            seen["texts"] = texts
            return np.ones((len(texts), 4), dtype=np.float32)

        out = build_catalog_items(self.inter, self.meta, self.items, use_text=True, encode_texts=fake_encode)
        self.assertEqual(out.text.shape, (len(self.items) + 1, 4))
        self.assertTrue((out.text[0] == 0).all())
        self.assertTrue(seen["texts"][0].startswith("Title cold"))

    def test_interacted_item_missing_from_catalog_fails_loudly(self) -> None:
        inter = interactions_from_sequences([_user("u3", ["i0", "ghost", "i1"])])
        with self.assertRaises(KeyError):
            build_catalog_items(inter, self.meta, self.items, use_text=False)


if __name__ == "__main__":
    unittest.main()
