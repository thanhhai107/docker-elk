from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import meilisearch
import psycopg
from elasticsearch import Elasticsearch, helpers

from backend.config import settings
from backend.ingest.load_products import load_products
from backend.ingest.load_reviews import load_reviews
from scripts.create_elasticsearch_indices import create_indices
from scripts.create_meilisearch_indexes import create_indexes


PRODUCT_COLUMNS = [
    "product_id",
    "title",
    "description",
    "category",
    "brand",
    "price",
    "rating",
    "review_count",
    "avg_review_rating",
    "loaded_review_count",
    "helpful_votes",
    "review_text",
]

REVIEW_COLUMNS = [
    "review_id",
    "product_id",
    "user_id",
    "rating",
    "title",
    "text",
    "helpful_vote",
    "timestamp",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest products and reviews into all search engines.")
    parser.add_argument("--products", type=Path, default=None, help="Product JSONL/GZ path.")
    parser.add_argument("--reviews", type=Path, default=None, help="Review JSONL/GZ path.")
    parser.add_argument("--product-limit", type=int, default=5000)
    parser.add_argument("--review-limit", type=int, default=20000)
    parser.add_argument("--reset", action="store_true")
    return parser.parse_args()


def default_path(*candidates: Path) -> Path:
    for path in candidates:
        if path.exists():
            return path
    return candidates[-1]


def enrich_products(products: list[dict[str, Any]], aggregates: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for product in products:
        aggregate = aggregates.get(
            product["product_id"],
            {
                "avg_review_rating": 0,
                "loaded_review_count": 0,
                "helpful_votes": 0,
                "review_text": "",
            },
        )
        enriched.append({**product, **aggregate})
    return enriched


def ingest_postgres(products: list[dict[str, Any]], reviews: list[dict[str, Any]], reset: bool) -> None:
    placeholders = ", ".join(["%s"] * len(PRODUCT_COLUMNS))
    product_sql = f"""
        INSERT INTO products ({", ".join(PRODUCT_COLUMNS)})
        VALUES ({placeholders})
        ON CONFLICT (product_id) DO UPDATE SET
            title = excluded.title,
            description = excluded.description,
            category = excluded.category,
            brand = excluded.brand,
            price = excluded.price,
            rating = excluded.rating,
            review_count = excluded.review_count,
            avg_review_rating = excluded.avg_review_rating,
            loaded_review_count = excluded.loaded_review_count,
            helpful_votes = excluded.helpful_votes,
            review_text = excluded.review_text
    """
    review_sql = f"""
        INSERT INTO reviews ({", ".join(REVIEW_COLUMNS)})
        VALUES ({", ".join(["%s"] * len(REVIEW_COLUMNS))})
        ON CONFLICT (review_id) DO NOTHING
    """
    with psycopg.connect(settings.postgres_dsn) as conn:
        if reset:
            conn.execute("TRUNCATE TABLE reviews, products RESTART IDENTITY")
        conn.executemany(product_sql, [[product.get(column) for column in PRODUCT_COLUMNS] for product in products])
        known_products = {product["product_id"] for product in products}
        review_rows = [
            [review.get(column) for column in REVIEW_COLUMNS]
            for review in reviews
            if review["product_id"] in known_products
        ]
        if review_rows:
            conn.executemany(review_sql, review_rows)


def ingest_elasticsearch(products: list[dict[str, Any]], reset: bool) -> None:
    create_indices(reset=reset)
    client = Elasticsearch(settings.elasticsearch_url, request_timeout=60)
    actions = [
        {"_index": "products", "_id": product["product_id"], "_source": product}
        for product in products
    ]
    helpers.bulk(client, actions, chunk_size=1000, request_timeout=120)
    client.indices.refresh(index="products")


def ingest_meilisearch(products: list[dict[str, Any]], reset: bool) -> None:
    client = meilisearch.Client(settings.meili_url, settings.meili_master_key)
    if reset:
        try:
            client.delete_index("products")
        except Exception:
            pass
        client.create_index("products", {"primaryKey": "product_id"})
    create_indexes()
    task = client.index("products").add_documents(products, primary_key="product_id")
    client.wait_for_task(task.task_uid, timeout_in_ms=120000)


def main() -> int:
    args = parse_args()
    data_dir = settings.data_dir
    product_path = args.products or default_path(
        data_dir / "products.jsonl",
        data_dir / "raw" / "meta_Electronics.jsonl.gz",
        data_dir / "sample" / "products.jsonl",
    )
    review_path = args.reviews or default_path(
        data_dir / "reviews.jsonl",
        data_dir / "raw" / "Electronics.jsonl.gz",
        data_dir / "sample" / "reviews.jsonl",
    )

    print(f"Products: {product_path}")
    print(f"Reviews:  {review_path if review_path.exists() else 'not found'}")
    products = load_products(product_path, args.product_limit)
    reviews, aggregates = load_reviews(review_path if review_path.exists() else None, args.review_limit)
    products = enrich_products(products, aggregates)

    print(f"Loaded {len(products)} products and {len(reviews)} reviews")
    ingest_postgres(products, reviews, args.reset)
    ingest_elasticsearch(products, args.reset)
    ingest_meilisearch(products, args.reset)
    print("Ingest complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
