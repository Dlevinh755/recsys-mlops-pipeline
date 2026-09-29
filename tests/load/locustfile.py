"""Phase 7 — generate real traffic against `serving` so the Grafana
"serving-overview" dashboard has something to show (`bao-cao-ky-thuat.md`
mục 3.2). Manual use only, not run in CI:

    pip install locust
    locust -f tests/load/locustfile.py --host http://localhost:8000

User/item ids are fetched from `serving` itself (`GET /users`, then a
product id out of that user's own homepage response) instead of hard-coded
— avoids drifting out of date if the seed dataset changes.
"""

from __future__ import annotations

import random

from locust import HttpUser, between, task


class ServingUser(HttpUser):
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        response = self.client.get("/users", name="/users")
        users = response.json().get("users", [])
        self.user_id = random.choice(users)["user_id"] if users else "u001"
        self.item_id: str | None = None

    @task(3)
    def homepage(self) -> None:
        response = self.client.get(
            "/recommend/homepage", params={"user_id": self.user_id}, name="/recommend/homepage"
        )
        items = response.json().get("items", [])
        if items:
            self.item_id = random.choice(items)["product_id"]

    @task(1)
    def similar(self) -> None:
        item_id = self.item_id or "p001"
        self.client.get("/recommend/similar", params={"item_id": item_id}, name="/recommend/similar")
