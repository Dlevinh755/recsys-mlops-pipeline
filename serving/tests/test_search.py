"""`GET /products/search` + pure `search_items` — no infra needed."""

from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from serving.app.api import search
from serving.app.core.fallback import search_items

ACTIVE = ["p1", "p2", "p3"]  # popularity order
META = {
    "p1": {"title": "Red Dead Redemption 2", "description": "Video Games · $19.98", "image_url": None},
    "p2": {"title": "Dark Souls III", "description": "Video Games · $19.49", "image_url": None},
    "p3": {"title": "DualShock 4 Controller", "description": "Accessories · $57.00", "image_url": None},
}


class SearchItemsTest(unittest.TestCase):
    def test_case_insensitive_match_keeps_popularity_order(self) -> None:
        self.assertEqual(search_items(ACTIVE, META, "VIDEO games", 10), ["p1", "p2"])

    def test_all_tokens_must_match(self) -> None:
        self.assertEqual(search_items(ACTIVE, META, "dark games", 10), ["p2"])

    def test_empty_query_returns_nothing(self) -> None:
        self.assertEqual(search_items(ACTIVE, META, "   ", 10), [])

    def test_limit_is_respected(self) -> None:
        self.assertEqual(search_items(ACTIVE, META, "games", 1), ["p1"])


class _FakeFallback:
    def search(self, query: str, limit: int) -> list[str]:
        return search_items(ACTIVE, META, query, limit)

    def metadata(self, product_id: str) -> dict:
        return META[product_id]


class SearchEndpointTest(unittest.TestCase):
    def _client(self) -> TestClient:
        app = FastAPI()
        app.state.fallback = _FakeFallback()
        app.include_router(search.router)
        return TestClient(app)

    def test_returns_matching_items(self) -> None:
        response = self._client().get("/products/search", params={"q": "souls"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([item["product_id"] for item in body["items"]], ["p2"])
        self.assertEqual(body["items"][0]["title"], "Dark Souls III")

    def test_no_match_is_empty_not_error(self) -> None:
        response = self._client().get("/products/search", params={"q": "zzz"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])


if __name__ == "__main__":
    unittest.main()
