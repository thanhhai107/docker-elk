from __future__ import annotations

from pydantic import BaseModel, Field


class Product(BaseModel):
    product_id: str
    title: str
    features: str = ""
    description: str = ""
    category: str = "Electronics"
    brand: str = "Unknown"
    price: float | None = None
    rating: float = 0
    review_count: int = 0
    average_rating: float = 0
    rating_number: int = 0
    avg_review_rating: float = 0
    loaded_review_count: int = 0
    helpful_votes: int = 0


class ProductHit(Product):
    score: float | None = None
    highlights: dict[str, list[str]] = Field(default_factory=dict)
