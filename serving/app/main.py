"""Phase 5 — FastAPI serving entrypoint. Starts the background model-poll
and fallback-refresh loops on startup (`bao-cao-ky-thuat.md` mục 7.3/8),
wires the 3 routers + `/metrics`, exposes a dependency-free `/health` for
the Docker healthcheck.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, Request
from feast import FeatureStore

from serving.app.api import history, homepage, interact, similar, users
from serving.app.core.config import load_settings
from serving.app.core.fallback import FallbackCatalog
from serving.app.core.metrics import instrument
from serving.app.core.model_loader import ModelLoader
from serving.app.core.user_catalog import UserCatalog


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.redis_client = redis.Redis(
        host=settings.redis_host, port=settings.redis_port, decode_responses=True
    )
    app.state.feature_store = FeatureStore(repo_path="feature_repo")
    app.state.fallback = FallbackCatalog(settings.fallback_refresh_interval_seconds, settings.homepage_top_n)
    app.state.user_catalog = UserCatalog(settings.fallback_refresh_interval_seconds, settings.user_list_size)
    app.state.model_loader = ModelLoader(settings.model_poll_interval_seconds)

    app.state.fallback.start()
    app.state.user_catalog.start()
    app.state.model_loader.start()
    try:
        yield
    finally:
        await app.state.fallback.stop()
        await app.state.user_catalog.stop()
        await app.state.model_loader.stop()


app = FastAPI(title="recommendation-mlops serving", lifespan=lifespan)
instrument(app)

app.include_router(homepage.router)
app.include_router(similar.router)
app.include_router(interact.router)
app.include_router(users.router)
app.include_router(history.router)


@app.get("/health")
def health(request: Request) -> dict[str, str | None]:
    # `model_version` surfaces `ModelLoader`'s internal state (Phase 5) for
    # observability/testing (Phase 8: `serving/tests/test_model_reload.py`
    # polls this to confirm a new `promote.py` version gets picked up
    # without a restart) — the field is optional info, never gates the
    # Docker healthcheck's pass/fail on a model being loaded yet.
    loaded = request.app.state.model_loader.get_current()
    return {"status": "ok", "model_version": loaded.version if loaded else None}
