from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class RecommendedItem(BaseModel):
    product_id: str
    score: float
    title: str | None = None
    description: str | None = None
    image_url: str | None = None


class HomepageResponse(BaseModel):
    user_id: str
    items: list[RecommendedItem]
    source: str  # "model" | "fallback"


class SimilarResponse(BaseModel):
    item_id: str
    items: list[str]
    source: str  # "similar_items" | "fallback"


class InteractRequest(BaseModel):
    user_id: str
    item_id: str
    event_type: str = Field(pattern="^(view|cart|purchase|rating)$")
    rating: float | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="after")
    def _rating_matches_event_type(self) -> "InteractRequest":
        # Mirrors the `interactions` table's CHECK constraint
        # (infra/source-db/init/schema.sql) — fail fast with 422 instead of
        # a raw Postgres constraint violation.
        if self.event_type == "rating" and self.rating is None:
            raise ValueError("rating is required when event_type='rating'")
        if self.event_type != "rating" and self.rating is not None:
            raise ValueError("rating must be omitted unless event_type='rating'")
        return self


class InteractResponse(BaseModel):
    status: str


class UserOption(BaseModel):
    user_id: str
    sequence_length: int  # số item trong lịch sử — dùng gợi ý user "có dữ liệu" cho demo


class UsersResponse(BaseModel):
    users: list[UserOption]


class HistoryItem(BaseModel):
    product_id: str
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    event_time: str | None = None
    source: str  # "history" (Feast, batch) | "session" (Redis, tức thời)


class HistoryResponse(BaseModel):
    user_id: str
    items: list[HistoryItem]
