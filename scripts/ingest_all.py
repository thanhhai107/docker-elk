from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import meilisearch
import psycopg
from elasticsearch import Elasticsearch, helpers
from elasticsearch.helpers import BulkIndexError

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


def log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{timestamp}] {message}", flush=True)


def log_done(label: str, started_at: float) -> None:
    log(f"{label} complete in {time.perf_counter() - started_at:.1f}s")


def format_limit(limit: int | None) -> str:
    return "all (--all)" if limit is None else str(limit)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest products and reviews into all search engines.")
    parser.add_argument("--products", type=Path, default=None, help="Product JSONL/GZ path.")
    parser.add_argument("--reviews", type=Path, default=None, help="Review JSONL/GZ path.")
    parser.add_argument("--product-limit", type=int, default=100_000, help="Max products to ingest. Default: 100000.")
    parser.add_argument("--review-limit", type=int, default=100_000, help="Max matching reviews to ingest. Default: 100000.")
    parser.add_argument("--es-bulk-chunk-size", type=int, default=250)
    parser.add_argument("--es-request-timeout", type=int, default=300)
    parser.add_argument("--es-max-retries", type=int, default=3)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Ingest every product and review found in the selected input files.",
    )
    parser.add_argument(
        "--engine",
        choices=["all", "elasticsearch", "meilisearch", "postgres"],
        default="all",
        help="Choose one engine to ingest, or all engines.",
    )
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    if args.all:
        args.product_limit = None
        args.review_limit = None
    return args


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
    started_at = time.perf_counter()
    log("PostgreSQL: ensuring schema")
    ensure_postgres_schema()
    with psycopg.connect(settings.postgres_dsn) as conn:
        if reset:
            log("PostgreSQL: reset requested, truncating reviews and products")
            conn.execute("TRUNCATE TABLE reviews, products RESTART IDENTITY")
        product_rows = [[product.get(column) for column in PRODUCT_COLUMNS] for product in products]
        log(f"PostgreSQL: inserting/upserting {len(product_rows)} products")
        for index, chunk in enumerate(chunks(product_rows, 5000), start=1):
            with conn.cursor() as cur:
                cur.executemany(product_sql, chunk)
            log_progress("PostgreSQL products", index, len(chunk), len(product_rows), 5000)
        known_products = {product["product_id"] for product in products}
        review_rows = [
            [review.get(column) for column in REVIEW_COLUMNS]
            for review in reviews
            if review["product_id"] in known_products
        ]
        log(f"PostgreSQL: inserting {len(review_rows)} reviews")
        for index, chunk in enumerate(chunks(review_rows, 5000), start=1):
            with conn.cursor() as cur:
                cur.executemany(review_sql, chunk)
            log_progress("PostgreSQL reviews", index, len(chunk), len(review_rows), 5000)
    log_done("PostgreSQL ingest", started_at)


def ingest_elasticsearch(
    products: list[dict[str, Any]],
    reset: bool,
    chunk_size: int,
    request_timeout: int,
    max_retries: int,
) -> None:
    started_at = time.perf_counter()
    log(f"Elasticsearch products: creating indices reset={reset}")
    create_indices(reset=reset)
    client = Elasticsearch(settings.elasticsearch_url, request_timeout=60)
    actions = [
        {"_index": "amazon_electronics_products", "_id": product["product_id"], "_source": product}
        for product in products
    ]
    log(f"Elasticsearch products: indexing {len(actions)} docs")
    if actions:
        run_bulk(client, actions, "Elasticsearch products", chunk_size, request_timeout, max_retries)
    log("Elasticsearch products: refreshing index")
    client.indices.refresh(index="amazon_electronics_products")
    log_done("Elasticsearch products ingest", started_at)


def ingest_elasticsearch_reviews(
    reviews: list[dict[str, Any]],
    known_products: set[str],
    chunk_size: int,
    request_timeout: int,
    max_retries: int,
) -> None:
    started_at = time.perf_counter()
    client = Elasticsearch(settings.elasticsearch_url, request_timeout=60)
    actions = [
        {"_index": "amazon_electronics_reviews", "_id": review["review_id"], "_source": review}
        for review in reviews
        if review["product_id"] in known_products
    ]
    log(f"Elasticsearch reviews: indexing {len(actions)} docs")
    if actions:
        run_bulk(client, actions, "Elasticsearch reviews", chunk_size, request_timeout, max_retries)
    log("Elasticsearch reviews: refreshing index")
    client.indices.refresh(index="amazon_electronics_reviews")
    log_done("Elasticsearch reviews ingest", started_at)


def run_bulk(
    client: Elasticsearch,
    actions: list[dict[str, Any]],
    label: str,
    chunk_size: int,
    request_timeout: int,
    max_retries: int,
) -> None:
    try:
        total = len(actions)
        indexed = 0
        for index, chunk in enumerate(chunks(actions, chunk_size), start=1):
            helpers.bulk(
                client.options(request_timeout=request_timeout),
                chunk,
                chunk_size=chunk_size,
                max_retries=max_retries,
                initial_backoff=2,
                max_backoff=30,
                retry_on_status=(429, 502, 503, 504),
                request_timeout=request_timeout,
            )
            indexed += len(chunk)
            log_progress(label, index, len(chunk), total, chunk_size, processed=indexed)
    except BulkIndexError as exc:
        log(f"{label} failed: {len(exc.errors)} bulk item errors")
        for error in exc.errors[:5]:
            log(str(error))
        raise


def ingest_meilisearch(products: list[dict[str, Any]], reset: bool) -> None:
    started_at = time.perf_counter()
    client = meilisearch.Client(settings.meili_url, settings.meili_master_key)
    if reset:
        log("Meilisearch: reset requested, deleting product/review indexes")
        for index_name in ["amazon_electronics_products", "amazon_electronics_reviews"]:
            try:
                wait_task(client, client.delete_index(index_name))
                log(f"Meilisearch: deleted {index_name}")
            except Exception:
                log(f"Meilisearch: {index_name} did not exist or could not be deleted")
                pass
        log("Meilisearch: creating product/review indexes")
        wait_task(client, client.create_index("amazon_electronics_products", {"primaryKey": "product_id"}))
        wait_task(client, client.create_index("amazon_electronics_reviews", {"primaryKey": "review_id"}))
    log("Meilisearch: applying index settings")
    create_indexes()
    products_index = client.index("amazon_electronics_products")
    log(f"Meilisearch products: indexing {len(products)} docs")
    processed = 0
    for index, chunk in enumerate(chunks(products, 1000), start=1):
        task = products_index.add_documents(chunk, primary_key="product_id")
        wait_task(client, task)
        processed += len(chunk)
        log_progress("Meilisearch products", index, len(chunk), len(products), 1000, processed=processed)
    log_done("Meilisearch products ingest", started_at)


def ingest_meilisearch_reviews(reviews: list[dict[str, Any]], known_products: set[str]) -> None:
    started_at = time.perf_counter()
    client = meilisearch.Client(settings.meili_url, settings.meili_master_key)
    review_docs = [review for review in reviews if review["product_id"] in known_products]
    if not review_docs:
        log("Meilisearch reviews: no docs to index")
        return
    reviews_index = client.index("amazon_electronics_reviews")
    log(f"Meilisearch reviews: indexing {len(review_docs)} docs")
    processed = 0
    for index, chunk in enumerate(chunks(review_docs, 1000), start=1):
        task = reviews_index.add_documents(chunk, primary_key="review_id")
        wait_task(client, task)
        processed += len(chunk)
        log_progress("Meilisearch reviews", index, len(chunk), len(review_docs), 1000, processed=processed)
    log_done("Meilisearch reviews ingest", started_at)


def chunks(items: list[Any], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def log_progress(
    label: str,
    chunk_index: int,
    chunk_count: int,
    total: int,
    chunk_size: int,
    processed: int | None = None,
) -> None:
    processed = processed if processed is not None else min(chunk_index * chunk_size, total)
    percent = (processed / total * 100) if total else 100
    log(f"{label}: chunk {chunk_index} wrote {chunk_count} rows/docs ({processed}/{total}, {percent:.1f}%)")


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
    started_at = time.perf_counter()
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

    log("Starting ingest")
    log(f"Products: {product_path}")
    log(f"Reviews:  {review_path if review_path.exists() else 'not found'}")
    log(f"Product limit: {format_limit(args.product_limit)}")
    log(f"Review limit: {format_limit(args.review_limit)}")
    load_started_at = time.perf_counter()
    log("Loading products")
    products = load_products(product_path, args.product_limit)
    log(f"Loaded {len(products)} products from source")
    known_products = {product["product_id"] for product in products}
    log("Loading reviews matching selected products")
    reviews, aggregates = load_reviews(
        review_path if review_path.exists() else None,
        args.review_limit,
        product_ids=known_products,
    )
    log(f"Loaded {len(reviews)} reviews and {len(aggregates)} product review aggregates")
    log("Enriching products and reviews")
    products = enrich_products(products, aggregates)
    enriched_reviews = enrich_reviews(reviews, products)
    log_done("Data load and enrichment", load_started_at)

    log(f"Loaded {len(products)} products and {len(reviews)} reviews")
    log(f"Engine: {args.engine}")
    log(
        "Elasticsearch bulk: "
        f"chunk_size={args.es_bulk_chunk_size}, "
        f"request_timeout={args.es_request_timeout}, "
        f"max_retries={args.es_max_retries}"
    )

    if args.engine in {"all", "postgres"}:
        log("Ingesting PostgreSQL...")
        ingest_postgres(products, reviews, args.reset)

    if args.engine in {"all", "elasticsearch"}:
        log("Ingesting Elasticsearch products...")
        ingest_elasticsearch(
            products,
            args.reset,
            args.es_bulk_chunk_size,
            args.es_request_timeout,
            args.es_max_retries,
        )
        log("Ingesting Elasticsearch reviews...")
        ingest_elasticsearch_reviews(
            enriched_reviews,
            known_products,
            args.es_bulk_chunk_size,
            args.es_request_timeout,
            args.es_max_retries,
        )

    if args.engine in {"all", "meilisearch"}:
        log("Ingesting Meilisearch products...")
        ingest_meilisearch(products, args.reset)
        log("Ingesting Meilisearch reviews...")
        ingest_meilisearch_reviews(enriched_reviews, known_products)

    log_done("Ingest", started_at)
    log("Ingest complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
