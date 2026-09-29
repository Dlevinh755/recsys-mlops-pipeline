"""`GET /users` — danh sách user mẫu (nhiều lịch sử nhất trước) cho UI demo."""

from __future__ import annotations

from fastapi import APIRouter, Request

from serving.app.schemas.models import UsersResponse

router = APIRouter()


@router.get("/users", response_model=UsersResponse)
def users(request: Request) -> UsersResponse:
    return UsersResponse(users=request.app.state.user_catalog.users())
