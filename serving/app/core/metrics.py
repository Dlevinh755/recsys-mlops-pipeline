"""Prometheus instrumentation, hand-rolled with `prometheus_client` directly
instead of `prometheus-fastapi-instrumentator` (the plan's original choice —
see `docs/de-xuat-trien-khai.md` Phase 5): that package pins
`starlette<1.0.0`, which conflicts with `feast==0.66.0`'s
`starlette>=1.0.1` requirement — a real, unresolvable dependency clash, not
a version-pinning mistake (see `docs/modules/phase-5-serving.md`).
`prometheus_client` has no web-framework dependency at all, sidestepping
the conflict; the middleware below covers exactly what
`bao-cao-ky-thuat.md` mục 9.1 asks for (request count, latency).
"""

from __future__ import annotations

import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "path", "status_code"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency in seconds", ["method", "path"]
)


def instrument(app: FastAPI) -> None:
    @app.middleware("http")
    async def _track_requests(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        path = request.url.path
        REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(duration)
        return response

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
