from __future__ import annotations

from pydantic import BaseModel


class Review(BaseModel):
    review_id: str
    product_id: str
    user_id: str = "anonymous"
    rating: float = 0
    title: str = ""
    text: str = ""
    helpful_vote: int = 0
    timestamp: int | None = None
