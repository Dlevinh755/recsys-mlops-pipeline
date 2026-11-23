"""`GET /products/search` — tìm sản phẩm trong toàn bộ catalog đã nạp sẵn
trong `FallbackCatalog` (từ `gold.item_features`); không query Iceberg khi có
request. Kết quả sắp theo độ phổ biến.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from serving.app.schemas.models import RecommendedItem, SearchResponse

router = APIRouter()


@router.get("/products/search", response_model=SearchResponse)
def search(request: Request, q: str = Query(default="", max_length=100), limit: int = Query(default=12, ge=1, le=50)) -> SearchResponse:
    fallback = request.app.state.fallback
    ids = fallback.search(q.strip(), limit)
    return SearchResponse(
        query=q.strip(),
        items=[RecommendedItem(product_id=pid, score=0.0, **fallback.metadata(pid)) for pid in ids],
    )
