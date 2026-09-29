"""Smoke tests for the 3 Definition-of-Done branches (cache hit, cache miss
with a real model, model/feature unavailable -> fallback) plus `/interact`.

Deliberately does **not** use `serving.app.main.app` (its `lifespan` opens
real Redis/Feast/MLflow/Iceberg connections) — builds a minimal app with the
same routers and fake `app.state`, so this runs with no infra at all.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from serving.app.api import history, homepage, interact, similar, users
from serving.app.core.config import Settings


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def rpush(self, key: str, value: str) -> None:
        self._lists.setdefault(key, []).append(value)

    def ltrim(self, key: str, start: int, end: int) -> None:
        lst = self._lists.get(key, [])
        self._lists[key] = lst[start:] if end == -1 else lst[start : end + 1]

    def expire(self, key: str, ttl_seconds: int) -> None:
        pass

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        lst = self._lists.get(key, [])
        return lst[start:] if end == -1 else lst[start : end + 1]


class FakeRanker:
    def __init__(self, scores_by_item: dict[str, float]) -> None:
        self._scores_by_item = scores_by_item

    def predict(self, user_id: str, candidate_items: list[str]) -> list[float]:
        return [self._scores_by_item.get(item, 0.0) for item in candidate_items]


class FakeFallback:
    def __init__(self, active: list[str]) -> None:
        self._active = active

    def active_items(self) -> list[str]:
        return self._active

    def popular_items(self) -> list[str]:
        return self._active[:2]

    def metadata(self, product_id: str) -> dict:
        return {"title": f"title-{product_id}", "description": "desc", "image_url": None}


class FakeUserCatalog:
    def users(self) -> list[dict]:
        return [{"user_id": "u1", "sequence_length": 5}]


class FakeModelLoader:
    def __init__(self, ranker=None) -> None:
        self._ranker = ranker

    def get_ranker(self, redis_client, feature_store):
        return self._ranker

    def get_current(self):
        return None


def _build_app(*, ranker, active_items: list[str]) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(
        redis_host="unused",
        redis_port=0,
        source_db_dsn="postgresql://unused",
        mlflow_tracking_uri="http://unused",
        model_poll_interval_seconds=60,
        fallback_refresh_interval_seconds=300,
        homepage_top_n=10,
        user_list_size=50,
        cache_ttl_seconds=60,
        similar_cache_ttl_seconds=300,
        recent_items_ttl_seconds=1800,
        recent_items_max_length=20,
    )
    app.state.redis_client = FakeRedis()
    app.state.feature_store = None  # never touched: ranker.predict() is faked
    app.state.fallback = FakeFallback(active_items)
    app.state.model_loader = FakeModelLoader(ranker)
    app.state.user_catalog = FakeUserCatalog()
    app.include_router(homepage.router)
    app.include_router(similar.router)
    app.include_router(interact.router)
    app.include_router(users.router)
    app.include_router(history.router)
    return app


class HomepageTest(unittest.TestCase):
    def test_cache_miss_with_model_returns_ranked_items(self) -> None:
        app = _build_app(
            ranker=FakeRanker({"p1": 1.0, "p2": 5.0, "p3": 3.0}),
            active_items=["p1", "p2", "p3"],
        )
        response = TestClient(app).get("/recommend/homepage", params={"user_id": "u1"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source"], "model")
        self.assertEqual([item["product_id"] for item in body["items"]], ["p2", "p3", "p1"])

    def test_cache_hit_returns_previously_cached_response(self) -> None:
        app = _build_app(ranker=FakeRanker({"p1": 9.0}), active_items=["p1"])
        client = TestClient(app)
        first = client.get("/recommend/homepage", params={"user_id": "u1"}).json()
        # Swap in a ranker that would produce a different result — cache hit
        # must still return the first response, proving it didn't recompute.
        app.state.model_loader = FakeModelLoader(FakeRanker({"p1": 0.1}))
        second = client.get("/recommend/homepage", params={"user_id": "u1"}).json()
        self.assertEqual(first, second)

    def test_no_model_falls_back_without_5xx(self) -> None:
        app = _build_app(ranker=None, active_items=["p1", "p2"])
        response = TestClient(app).get("/recommend/homepage", params={"user_id": "cold-start-user"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "fallback")

    def test_empty_catalog_falls_back_without_5xx(self) -> None:
        app = _build_app(ranker=FakeRanker({}), active_items=[])
        response = TestClient(app).get("/recommend/homepage", params={"user_id": "u1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "fallback")


class SimilarTest(unittest.TestCase):
    def test_missing_similar_items_falls_back(self) -> None:
        app = _build_app(ranker=None, active_items=["p1", "p2"])
        response = TestClient(app).get("/recommend/similar", params={"item_id": "unknown-item"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "fallback")

    def test_materialized_similar_items_are_returned(self) -> None:
        app = _build_app(ranker=None, active_items=["p1", "p2"])
        app.state.redis_client.set("similar_items:p1", json.dumps(["p2", "p3"]))
        response = TestClient(app).get("/recommend/similar", params={"item_id": "p1"})
        body = response.json()
        self.assertEqual(body["source"], "similar_items")
        self.assertEqual(body["items"], ["p2", "p3"])


class InteractTest(unittest.TestCase):
    def test_interact_invalidates_homepage_cache_for_that_user(self) -> None:
        app = _build_app(ranker=FakeRanker({"p1": 1.0}), active_items=["p1"])
        client = TestClient(app)
        client.get("/recommend/homepage", params={"user_id": "u1"})  # populate cache
        self.assertIsNotNone(app.state.redis_client.get("cache:homepage:u1"))

        with patch("serving.app.api.interact.psycopg.connect"), patch(
            "serving.app.api.interact.source_db_writer.write_interaction"
        ):
            response = client.post(
                "/interact", json={"user_id": "u1", "item_id": "p1", "event_type": "purchase"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(app.state.redis_client.get("cache:homepage:u1"))
        self.assertEqual(app.state.redis_client.lrange("recent_items:u1", 0, -1), ["p1"])

    def test_unknown_entity_returns_404(self) -> None:
        from serving.app.core.source_db_writer import UnknownEntityError

        app = _build_app(ranker=None, active_items=[])
        client = TestClient(app)
        with patch("serving.app.api.interact.psycopg.connect"), patch(
            "serving.app.api.interact.source_db_writer.write_interaction",
            side_effect=UnknownEntityError("user_id 'ghost' does not exist"),
        ):
            response = client.post(
                "/interact", json={"user_id": "ghost", "item_id": "p1", "event_type": "view"}
            )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()


class UsersTest(unittest.TestCase):
    def test_users_endpoint_returns_catalog(self) -> None:
        app = _build_app(ranker=None, active_items=["p1"])
        response = TestClient(app).get("/users")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"users": [{"user_id": "u1", "sequence_length": 5}]})


class _FakeOnline:
    def __init__(self, data: dict) -> None:
        self._data = data

    def to_dict(self) -> dict:
        return self._data


class FakeFeatureStore:
    def get_online_features(self, features, entity_rows):
        return _FakeOnline({"item_sequence": [["h1", "h2"]], "event_time_sequence": [[None, None]]})


class HistoryTest(unittest.TestCase):
    def test_history_merges_session_and_batch_newest_first(self) -> None:
        app = _build_app(ranker=None, active_items=["p1"])
        app.state.feature_store = FakeFeatureStore()
        app.state.redis_client.rpush("recent_items:u1", "s1")
        response = TestClient(app).get("/users/u1/history")
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual([i["product_id"] for i in items], ["s1", "h2", "h1"])
        self.assertEqual(items[0]["source"], "session")
        self.assertEqual(items[0]["title"], "title-s1")

    def test_history_survives_feast_failure(self) -> None:
        app = _build_app(ranker=None, active_items=["p1"])  # feature_store=None -> AttributeError
        response = TestClient(app).get("/users/u1/history")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])
