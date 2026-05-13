from __future__ import annotations

from pathlib import Path

from backend.ingest.normalize import aggregate_reviews, iter_jsonl, normalize_review


def load_reviews(
    path: Path | None,
    limit: int | None = None,
    product_ids: set[str] | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    if not path or not path.exists():
        return [], {}
    reviews = []
    for raw in iter_jsonl(path):
        review = normalize_review(raw)
        if not review:
            continue
        if product_ids is not None and review["product_id"] not in product_ids:
            continue
        reviews.append(review)
        if limit is not None and len(reviews) >= limit:
            break
    return reviews, aggregate_reviews(reviews)
