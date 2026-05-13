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
    "features",
    "description",
    "category",
    "brand",
    "price",
    "rating",
    "review_count",
    "average_rating",
    "rating_number",
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
    "verified_purchase",
    "timestamp",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest products and reviews into all search engines.")
    parser.add_argument("--products", type=Path, default=None, help="Product JSONL/GZ path.")
    parser.add_argument("--reviews", type=Path, default=None, help="Review JSONL/GZ path.")
    parser.add_argument("--product-limit", type=int, default=5000)
    parser.add_argument("--review-limit", type=int, default=20000)
    parser.add_argument(
        "--engine",
        choices=["all", "elasticsearch", "meilisearch", "postgres"],
        default="all",
        help="Choose one engine to ingest, or all engines.",
    )
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
        merged = {**product, **aggregate}
        merged["average_rating"] = merged.get("average_rating", merged.get("rating", 0))
        merged["rating_number"] = merged.get("rating_number", merged.get("review_count", 0))
        enriched.append(merged)
    return enriched


def enrich_reviews(reviews: list[dict[str, Any]], products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    product_lookup = {product["product_id"]: product for product in products}
    enriched = []
    for review in reviews:
        product = product_lookup.get(review["product_id"], {})
        enriched.append(
            {
                **review,
                "brand": product.get("brand", "Unknown"),
                "category": product.get("category", "Electronics"),
            }
        )
    return enriched


def ensure_postgres_schema() -> None:
    schema_path = Path(__file__).resolve().parent / "init_postgres.sql"
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute(schema_path.read_text(encoding="utf-8"))


def ingest_postgres(products: list[dict[str, Any]], reviews: list[dict[str, Any]], reset: bool) -> None:
    placeholders = ", ".join(["%s"] * len(PRODUCT_COLUMNS))
    product_sql = f"""
        INSERT INTO products ({", ".join(PRODUCT_COLUMNS)})
        VALUES ({placeholders})
        ON CONFLICT (product_id) DO UPDATE SET
            title = excluded.title,
            features = excluded.features,
            description = excluded.description,
            category = excluded.category,
            brand = excluded.brand,
            price = excluded.price,
            rating = excluded.rating,
            review_count = excluded.review_count,
            average_rating = excluded.average_rating,
            rating_number = excluded.rating_number,
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
    ensure_postgres_schema()
    with psycopg.connect(settings.postgres_dsn) as conn:
        if reset:
            conn.execute("TRUNCATE TABLE reviews, products RESTART IDENTITY")
        with conn.cursor() as cur:
            cur.executemany(
                product_sql,
                [[product.get(column) for column in PRODUCT_COLUMNS] for product in products],
            )
        known_products = {product["product_id"] for product in products}
        review_rows = [
            [review.get(column) for column in REVIEW_COLUMNS]
            for review in reviews
            if review["product_id"] in known_products
        ]
        if review_rows:
            with conn.cursor() as cur:
                cur.executemany(review_sql, review_rows)


def ingest_elasticsearch(products: list[dict[str, Any]], reset: bool) -> None:
    create_indices(reset=reset)
    client = Elasticsearch(settings.elasticsearch_url, request_timeout=60)
    actions = [
        {"_index": "amazon_electronics_products", "_id": product["product_id"], "_source": product}
        for product in products
    ]
    if actions:
        helpers.bulk(client.options(request_timeout=120), actions, chunk_size=1000)
    client.indices.refresh(index="amazon_electronics_products")


def ingest_elasticsearch_reviews(reviews: list[dict[str, Any]], known_products: set[str]) -> None:
    client = Elasticsearch(settings.elasticsearch_url, request_timeout=60)
    actions = [
        {"_index": "amazon_electronics_reviews", "_id": review["review_id"], "_source": review}
        for review in reviews
        if review["product_id"] in known_products
    ]
    if actions:
        helpers.bulk(client.options(request_timeout=120), actions, chunk_size=1000)
    client.indices.refresh(index="amazon_electronics_reviews")


def ingest_meilisearch(products: list[dict[str, Any]], reset: bool) -> None:
    client = meilisearch.Client(settings.meili_url, settings.meili_master_key)
    if reset:
        for index_name in ["amazon_electronics_products", "amazon_electronics_reviews"]:
            try:
                wait_task(client, client.delete_index(index_name))
            except Exception:
                pass
        wait_task(client, client.create_index("amazon_electronics_products", {"primaryKey": "product_id"}))
        wait_task(client, client.create_index("amazon_electronics_reviews", {"primaryKey": "review_id"}))
    create_indexes()
    products_index = client.index("amazon_electronics_products")
    for chunk in chunks(products, 1000):
        task = products_index.add_documents(chunk, primary_key="product_id")
        wait_task(client, task)


def ingest_meilisearch_reviews(reviews: list[dict[str, Any]], known_products: set[str]) -> None:
    client = meilisearch.Client(settings.meili_url, settings.meili_master_key)
    review_docs = [review for review in reviews if review["product_id"] in known_products]
    if not review_docs:
        return
    reviews_index = client.index("amazon_electronics_reviews")
    for chunk in chunks(review_docs, 1000):
        task = reviews_index.add_documents(chunk, primary_key="review_id")
        wait_task(client, task)


def chunks(items: list[dict[str, Any]], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def task_uid(task: Any) -> int:
    if hasattr(task, "task_uid"):
        return task.task_uid
    return int(task["taskUid"])


def wait_task(client: meilisearch.Client, task: Any) -> None:
    completed = client.wait_for_task(task_uid(task), timeout_in_ms=120000)
    status = getattr(completed, "status", None) or completed.get("status")
    if status == "failed":
        error = getattr(completed, "error", None) or completed.get("error")
        raise RuntimeError(f"Meilisearch task failed: {error}")


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
    known_products = {product["product_id"] for product in products}
    reviews, aggregates = load_reviews(
        review_path if review_path.exists() else None,
        args.review_limit,
        product_ids=known_products,
    )
    products = enrich_products(products, aggregates)
    enriched_reviews = enrich_reviews(reviews, products)

    print(f"Loaded {len(products)} products and {len(reviews)} reviews")
    print(f"Engine: {args.engine}")

    if args.engine in {"all", "postgres"}:
        print("Ingesting PostgreSQL...")
        ingest_postgres(products, reviews, args.reset)

    if args.engine in {"all", "elasticsearch"}:
        print("Ingesting Elasticsearch products...")
        ingest_elasticsearch(products, args.reset)
        print("Ingesting Elasticsearch reviews...")
        ingest_elasticsearch_reviews(enriched_reviews, known_products)

    if args.engine in {"all", "meilisearch"}:
        print("Ingesting Meilisearch products...")
        ingest_meilisearch(products, args.reset)
        print("Ingesting Meilisearch reviews...")
        ingest_meilisearch_reviews(enriched_reviews, known_products)

    print("Ingest complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
