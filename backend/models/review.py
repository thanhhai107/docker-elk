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
    verified_purchase: bool = False
    timestamp: int | None = None
    product_title: str = ""
    brand: str = "Unknown"
    category: str = "Electronics"
