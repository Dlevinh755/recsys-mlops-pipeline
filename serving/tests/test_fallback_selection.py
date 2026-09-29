from __future__ import annotations

import unittest

from serving.app.core.fallback import rank_items_by_popularity


class RankItemsByPopularityTest(unittest.TestCase):
    def test_sorts_by_purchases_then_views_descending(self) -> None:
        rows = [
            {"product_id": "low", "num_purchases": 1, "num_views": 100},
            {"product_id": "high", "num_purchases": 10, "num_views": 5},
            {"product_id": "mid", "num_purchases": 5, "num_views": 5},
        ]
        self.assertEqual(rank_items_by_popularity(rows), ["high", "mid", "low"])

    def test_missing_counts_treated_as_zero(self) -> None:
        rows = [
            {"product_id": "no_data", "num_purchases": None, "num_views": None},
            {"product_id": "some_views", "num_purchases": 0, "num_views": 3},
        ]
        self.assertEqual(rank_items_by_popularity(rows), ["some_views", "no_data"])

    def test_empty_catalog_yields_empty_list(self) -> None:
        self.assertEqual(rank_items_by_popularity([]), [])


if __name__ == "__main__":
    unittest.main()


class BuildItemMetadataTest(unittest.TestCase):
    def test_description_combines_category_and_price(self) -> None:
        from serving.app.core.fallback import build_item_metadata

        meta = build_item_metadata({"title": "T", "category": "Phones", "price": 12.5, "image_url": "u"})
        self.assertEqual(meta, {"title": "T", "description": "Phones · $12.50", "image_url": "u"})

    def test_description_falls_back_to_category_without_price(self) -> None:
        from serving.app.core.fallback import build_item_metadata

        self.assertEqual(build_item_metadata({"title": "T", "category": "Phones"})["description"], "Phones")


class RankUsersTest(unittest.TestCase):
    def test_most_active_first_and_truncated(self) -> None:
        from serving.app.core.user_catalog import rank_users_by_activity

        rows = [
            {"user_id": "b", "sequence_length": 3},
            {"user_id": "a", "sequence_length": 3},
            {"user_id": "c", "sequence_length": 10},
        ]
        self.assertEqual([u["user_id"] for u in rank_users_by_activity(rows, 2)], ["c", "a"])
