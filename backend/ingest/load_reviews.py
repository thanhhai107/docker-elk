from __future__ import annotations

from pathlib import Path

from backend.ingest.normalize import aggregate_reviews, iter_jsonl, normalize_review


def load_reviews(path: Path | None, limit: int | None = None) -> tuple[list[dict], dict[str, dict]]:
    if not path or not path.exists():
        return [], {}
    reviews = []
    for raw in iter_jsonl(path, limit):
        review = normalize_review(raw)
        if review:
            reviews.append(review)
    return reviews, aggregate_reviews(reviews)
