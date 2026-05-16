from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.ingest.normalize import (
    aggregate_reviews,
    iter_jsonl,
    normalize_product,
    normalize_review,
)


PROGRESS_LOG_INTERVAL = 50_000


def log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{timestamp}] {message}", flush=True)


def log_done(label: str, started_at: float) -> None:
    log(f"{label} complete in {time.perf_counter() - started_at:.1f}s")


def format_limit(limit: int | None) -> str:
    return "all" if limit is None else str(limit)


def semantic_text(product: dict[str, Any]) -> str:
    return " ".join(
        str(product.get(field) or "")
        for field in ["title", "brand", "category", "features", "description", "review_text"]
    )


def load_products_with_tracking(path: Path, limit: int | None) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    scanned = 0
    next_log_at = PROGRESS_LOG_INTERVAL
    for raw in iter_jsonl(path):
        scanned += 1
        product = normalize_product(raw)
        if product:
            products.append(product)
        if len(products) >= next_log_at:
            log(f"Products load: scanned {scanned} rows, accepted {len(products)} products")
            next_log_at += PROGRESS_LOG_INTERVAL
        if limit is not None and len(products) >= limit:
            break
    log(f"Products load: done scanned {scanned} rows, accepted {len(products)} products")
    return products


def load_reviews_with_tracking(
    path: Path | None,
    product_ids: set[str] | None,
    max_reviews_per_product: int | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not path or not path.exists():
        log("Reviews load: source not found, accepted 0 reviews")
        return [], {}
    reviews: list[dict[str, Any]] = []
    reviews_by_product: dict[str, int] = {}
    scanned = 0
    normalized = 0
    matched_products = 0
    skipped_per_product_cap = 0
    next_log_at = PROGRESS_LOG_INTERVAL
    for raw in iter_jsonl(path):
        scanned += 1
        review = normalize_review(raw)
        if not review:
            continue
        normalized += 1
        if product_ids is not None and review["product_id"] not in product_ids:
            if scanned >= next_log_at:
                log(
                    "Reviews load: "
                    f"scanned {scanned} rows, normalized {normalized}, "
                    f"matched products {matched_products}, accepted {len(reviews)} reviews"
                )
                next_log_at += PROGRESS_LOG_INTERVAL
            continue
        matched_products += 1
        product_review_count = reviews_by_product.get(review["product_id"], 0)
        if max_reviews_per_product is not None and product_review_count >= max_reviews_per_product:
            skipped_per_product_cap += 1
            if scanned >= next_log_at:
                log(
                    "Reviews load: "
                    f"scanned {scanned} rows, normalized {normalized}, "
                    f"matched products {matched_products}, accepted {len(reviews)} reviews, "
                    f"skipped per-product cap {skipped_per_product_cap}"
                )
                next_log_at += PROGRESS_LOG_INTERVAL
            continue
        reviews.append(review)
        reviews_by_product[review["product_id"]] = product_review_count + 1
        if len(reviews) >= next_log_at:
            log(
                "Reviews load: "
                f"scanned {scanned} rows, normalized {normalized}, "
                f"matched products {matched_products}, accepted {len(reviews)} reviews, "
                f"skipped per-product cap {skipped_per_product_cap}"
            )
            next_log_at += PROGRESS_LOG_INTERVAL
    log(
        "Reviews load: done "
        f"scanned {scanned} rows, normalized {normalized}, "
        f"matched products {matched_products}, accepted {len(reviews)} reviews, "
        f"products with reviews {len(reviews_by_product)}, "
        f"skipped per-product cap {skipped_per_product_cap}"
    )
    return reviews, aggregate_reviews(reviews)


def enrich_products(
    products: list[dict[str, Any]],
    aggregates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    default_aggregate = {
        "avg_review_rating": 0,
        "loaded_review_count": 0,
        "helpful_votes": 0,
        "review_text": "",
    }
    for product in products:
        aggregate = aggregates.get(product["product_id"], default_aggregate)
        merged = {**product, **aggregate}
        merged["average_rating"] = merged.get("average_rating", merged.get("rating", 0))
        merged["rating_number"] = merged.get("rating_number", merged.get("review_count", 0))
        merged["title_suggest"] = merged.get("title", "")
        merged["semantic_text"] = semantic_text(merged)
        enriched.append(merged)
    return enriched


def enrich_reviews(
    reviews: list[dict[str, Any]],
    products: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    product_lookup = {product["product_id"]: product for product in products}
    enriched: list[dict[str, Any]] = []
    for review in reviews:
        product = product_lookup.get(review["product_id"], {})
        enriched.append(
            {
                **review,
                "product_title": product.get("title", ""),
                "brand": product.get("brand", "Unknown"),
                "category": product.get("category", "Electronics"),
            }
        )
    return enriched


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")
            count += 1
    return count


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records