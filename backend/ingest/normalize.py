from __future__ import annotations

import gzip
import json
import re
from collections import defaultdict
from hashlib import sha1
from pathlib import Path
from typing import Any, Iterable


def iter_jsonl(path: Path, limit: int | None = None) -> Iterable[dict[str, Any]]:
    opener = gzip.open if ".gz" in path.suffixes else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if limit is not None and index >= limit:
                break
            if line.strip():
                yield json.loads(line)


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(as_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(as_text(item) for item in value.values())
    return str(value)


def parse_number(value: Any, default: float = 0) -> float:
    if value is None:
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else default


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def normalize_product(raw: dict[str, Any]) -> dict[str, Any] | None:
    product_id = raw.get("product_id") or raw.get("parent_asin") or raw.get("asin")
    title = raw.get("title")
    if not product_id or not title:
        return None
    categories = raw.get("categories") or []
    details = raw.get("details") or {}
    features = as_text(raw.get("features"))
    description = " ".join(
        part for part in [as_text(raw.get("description")), features] if part
    )
    average_rating = parse_number(raw.get("average_rating") or raw.get("rating"), 0)
    rating_number = int(parse_number(raw.get("rating_number") or raw.get("review_count"), 0))
    return {
        "product_id": str(product_id),
        "title": str(title),
        "features": features,
        "description": description or str(title),
        "category": str(
            raw.get("category")
            or (categories[-1] if categories else None)
            or raw.get("main_category")
            or "Electronics"
        ),
        "brand": str(
            raw.get("brand")
            or raw.get("store")
            or details.get("Brand")
            or details.get("Manufacturer")
            or "Unknown"
        ),
        "price": parse_number(raw.get("price"), 0),
        "rating": average_rating,
        "review_count": rating_number,
        "average_rating": average_rating,
        "rating_number": rating_number,
    }


def normalize_review(raw: dict[str, Any]) -> dict[str, Any] | None:
    product_id = raw.get("parent_asin") or raw.get("asin") or raw.get("product_id")
    if not product_id:
        return None
    user_id = str(raw.get("user_id") or raw.get("reviewerID") or "anonymous")
    timestamp = raw.get("timestamp")
    review_key = f"{product_id}:{user_id}:{timestamp or raw.get('title', '')}:{raw.get('text', '')[:80]}"
    review_id = str(raw.get("review_id") or raw.get("id") or sha1(review_key.encode("utf-8")).hexdigest())
    return {
        "review_id": review_id,
        "product_id": str(product_id),
        "user_id": user_id,
        "rating": parse_number(raw.get("rating") or raw.get("overall"), 0),
        "title": as_text(raw.get("title") or raw.get("summary")),
        "text": as_text(raw.get("text") or raw.get("reviewText")),
        "helpful_vote": int(parse_number(raw.get("helpful_vote"), 0)),
        "verified_purchase": parse_bool(raw.get("verified_purchase", raw.get("verified"))),
        "timestamp": int(timestamp) if str(timestamp or "").isdigit() else None,
    }


def aggregate_reviews(reviews: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"loaded_review_count": 0, "rating_sum": 0.0, "helpful_votes": 0, "texts": []}
    )
    for review in reviews:
        item = grouped[review["product_id"]]
        item["loaded_review_count"] += 1
        item["rating_sum"] += review["rating"]
        item["helpful_votes"] += review["helpful_vote"]
        if review["text"] and len(item["texts"]) < 5:
            item["texts"].append(review["text"])
    for item in grouped.values():
        count = item["loaded_review_count"]
        item["avg_review_rating"] = round(item["rating_sum"] / count, 2) if count else 0
        item["review_text"] = " ".join(item["texts"])
        del item["rating_sum"]
        del item["texts"]
    return grouped
