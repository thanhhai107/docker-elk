from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import meilisearch
import psycopg
from elasticsearch import Elasticsearch, helpers
from elasticsearch.helpers import BulkIndexError

from backend.config import settings
from backend.ingest.pipeline import log, log_done, read_jsonl
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

DEFAULT_ES_BULK_CHUNK_SIZE = 500
DEFAULT_ES_REQUEST_TIMEOUT = 600
DEFAULT_ES_MAX_RETRIES = 5
DEFAULT_MEILI_CHUNK_SIZE = 2000
DEFAULT_POSTGRES_CHUNK_SIZE = 5000

PROCESSED_PRODUCTS_FILENAME = "products.jsonl"
PROCESSED_REVIEWS_FILENAME = "reviews.jsonl"
MANIFEST_FILENAME = "manifest.json"


def meili_product_doc(product: dict[str, Any]) -> dict[str, Any]:
    doc = dict(product)
    doc.pop("semantic_text", None)
    doc.pop("title_suggest", None)
    return doc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest pre-processed products and reviews into search engines.",
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=None,
        help="Directory containing processed JSONL produced by prepare_data.py. Default: <data_dir>/processed.",
    )
    parser.add_argument(
        "--products",
        type=Path,
        default=None,
        help="Override processed products JSONL path.",
    )
    parser.add_argument(
        "--reviews",
        type=Path,
        default=None,
        help="Override processed reviews JSONL path.",
    )
    parser.add_argument(
        "--es-bulk-chunk-size",
        type=int,
        default=DEFAULT_ES_BULK_CHUNK_SIZE,
        help=f"Elasticsearch bulk chunk size. Default: {DEFAULT_ES_BULK_CHUNK_SIZE}.",
    )
    parser.add_argument(
        "--es-request-timeout",
        type=int,
        default=DEFAULT_ES_REQUEST_TIMEOUT,
        help=f"Elasticsearch request timeout in seconds. Default: {DEFAULT_ES_REQUEST_TIMEOUT}.",
    )
    parser.add_argument(
        "--es-max-retries",
        type=int,
        default=DEFAULT_ES_MAX_RETRIES,
        help=f"Elasticsearch bulk retry count. Default: {DEFAULT_ES_MAX_RETRIES}.",
    )
    parser.add_argument(
        "--meili-chunk-size",
        type=int,
        default=DEFAULT_MEILI_CHUNK_SIZE,
        help=f"Meilisearch add_documents chunk size. Default: {DEFAULT_MEILI_CHUNK_SIZE}.",
    )
    parser.add_argument(
        "--postgres-chunk-size",
        type=int,
        default=DEFAULT_POSTGRES_CHUNK_SIZE,
        help=f"PostgreSQL executemany chunk size. Default: {DEFAULT_POSTGRES_CHUNK_SIZE}.",
    )
    parser.add_argument(
        "--engine",
        choices=["all", "elasticsearch", "meilisearch", "postgres"],
        default="all",
        help="Choose one engine to ingest, or all engines.",
    )
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--skip-embeddings", action="store_true", help="Skip Vertex AI embedding generation for Elasticsearch products.")
    parser.add_argument("--limit", type=int, default=0, help="Limit the number of products/reviews to ingest (for testing). 0 means no limit.")
    return parser.parse_args()


def resolve_processed_dir(arg: Path | None) -> Path:
    if arg is not None:
        return arg
    return settings.data_dir / "processed"


def load_processed(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    processed_dir = resolve_processed_dir(args.processed_dir)
    products_path = args.products or processed_dir / PROCESSED_PRODUCTS_FILENAME
    reviews_path = args.reviews or processed_dir / PROCESSED_REVIEWS_FILENAME
    manifest_path = processed_dir / MANIFEST_FILENAME

    if not products_path.exists():
        raise SystemExit(
            f"Processed products file not found: {products_path}. "
            "Run scripts/prepare_data.py first."
        )
    if not reviews_path.exists():
        raise SystemExit(
            f"Processed reviews file not found: {reviews_path}. "
            "Run scripts/prepare_data.py first."
        )

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            log(
                "Manifest: "
                f"products={manifest.get('product_count')}, "
                f"reviews={manifest.get('review_count')}, "
                f"product_limit={manifest.get('product_limit')}, "
                f"max_reviews_per_product={manifest.get('max_reviews_per_product')}"
            )
        except (json.JSONDecodeError, OSError) as exc:
            log(f"Manifest: failed to parse {manifest_path}: {exc}")
    else:
        log(f"Manifest: not found at {manifest_path}, continuing without it")

    log(f"Reading processed products from {products_path}")
    products = read_jsonl(products_path)
    log(f"Read {len(products)} products")
    log(f"Reading processed reviews from {reviews_path}")
    reviews = read_jsonl(reviews_path)
    log(f"Read {len(reviews)} reviews")
    return products, reviews


def ensure_postgres_schema() -> None:
    schema_path = Path(__file__).resolve().parent / "init_postgres.sql"
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute(schema_path.read_text(encoding="utf-8"))


def ingest_postgres(
    products: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    reset: bool,
    chunk_size: int,
) -> None:
    placeholders = ", ".join(["%s"] * len(PRODUCT_COLUMNS))
    product_sql = f"""
        INSERT INTO products ({', '.join(PRODUCT_COLUMNS)})
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
        INSERT INTO reviews ({', '.join(REVIEW_COLUMNS)})
        VALUES ({', '.join(['%s'] * len(REVIEW_COLUMNS))})
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
        for index, chunk in enumerate(chunks(product_rows, chunk_size), start=1):
            with conn.cursor() as cur:
                cur.executemany(product_sql, chunk)
            log_progress("PostgreSQL products", index, len(chunk), len(product_rows), chunk_size)
        known_products = {product["product_id"] for product in products}
        review_rows = [
            [review.get(column) for column in REVIEW_COLUMNS]
            for review in reviews
            if review["product_id"] in known_products
        ]
        log(f"PostgreSQL: inserting {len(review_rows)} reviews")
        for index, chunk in enumerate(chunks(review_rows, chunk_size), start=1):
            with conn.cursor() as cur:
                cur.executemany(review_sql, chunk)
            log_progress("PostgreSQL reviews", index, len(chunk), len(review_rows), chunk_size)
    log_done("PostgreSQL ingest", started_at)


def _build_embedding_text(product: dict[str, Any]) -> str:
    parts = [product.get("title", "")]
    features = product.get("features", "")
    if features:
        parts.append(features[:200])
    description = product.get("description", "")
    if description:
        parts.append(description[:200])
    return " ".join(parts)


def _generate_embeddings(products: list[dict[str, Any]]) -> None:
    from backend.services.vertex_embedding import embed_texts

    texts = [_build_embedding_text(p) for p in products]
    log(f"Generating Vertex AI embeddings for {len(texts)} products...")
    embeddings = embed_texts(texts)
    for product, embedding in zip(products, embeddings):
        product["title_embedding"] = embedding
    log(f"Vertex AI embeddings generated for {len(embeddings)} products")


def ingest_elasticsearch(
    products: list[dict[str, Any]],
    reset: bool,
    chunk_size: int,
    request_timeout: int,
    max_retries: int,
    skip_embeddings: bool = False,
) -> None:
    started_at = time.perf_counter()
    log(f"Elasticsearch products: creating indices reset={reset}")
    create_indices(reset=reset)
    if not skip_embeddings:
        _generate_embeddings(products)
    else:
        log("Skipping Vertex AI embedding generation (--skip-embeddings)")
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


def ingest_meilisearch(products: list[dict[str, Any]], reset: bool, chunk_size: int) -> None:
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
        log("Meilisearch: creating product/review indexes")
        wait_task(client, client.create_index("amazon_electronics_products", {"primaryKey": "product_id"}))
        wait_task(client, client.create_index("amazon_electronics_reviews", {"primaryKey": "review_id"}))
    log("Meilisearch: applying index settings")
    create_indexes()
    products_index = client.index("amazon_electronics_products")
    log(f"Meilisearch products: indexing {len(products)} docs")
    processed = 0
    for index, chunk in enumerate(chunks(products, chunk_size), start=1):
        task = products_index.add_documents([meili_product_doc(product) for product in chunk], primary_key="product_id")
        wait_task(client, task)
        processed += len(chunk)
        log_progress("Meilisearch products", index, len(chunk), len(products), chunk_size, processed=processed)
    log_done("Meilisearch products ingest", started_at)


def ingest_meilisearch_reviews(reviews: list[dict[str, Any]], known_products: set[str], chunk_size: int) -> None:
    started_at = time.perf_counter()
    client = meilisearch.Client(settings.meili_url, settings.meili_master_key)
    review_docs = [review for review in reviews if review["product_id"] in known_products]
    if not review_docs:
        log("Meilisearch reviews: no docs to index")
        return
    reviews_index = client.index("amazon_electronics_reviews")
    log(f"Meilisearch reviews: indexing {len(review_docs)} docs")
    processed = 0
    for index, chunk in enumerate(chunks(review_docs, chunk_size), start=1):
        task = reviews_index.add_documents(chunk, primary_key="review_id")
        wait_task(client, task)
        processed += len(chunk)
        log_progress("Meilisearch reviews", index, len(chunk), len(review_docs), chunk_size, processed=processed)
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
    log("Starting ingest")
    products, reviews = load_processed(args)
    known_products = {product["product_id"] for product in products}
    log(f"Engine: {args.engine}")
    log(
        "Elasticsearch bulk: "
        f"chunk_size={args.es_bulk_chunk_size}, "
        f"request_timeout={args.es_request_timeout}, "
        f"max_retries={args.es_max_retries}"
    )
    log(f"Meilisearch chunk_size={args.meili_chunk_size}")
    log(f"PostgreSQL chunk_size={args.postgres_chunk_size}")

    if args.limit > 0:
        log(f"LIMITING ingest to {args.limit} items (--limit {args.limit})")
        products = products[:args.limit]
        reviews = reviews[:args.limit]
        
    if args.engine in {"all", "postgres"}:
        log("Ingesting PostgreSQL...")
        ingest_postgres(products, reviews, args.reset, args.postgres_chunk_size)

    if args.engine in {"all", "elasticsearch"}:
        log("Ingesting Elasticsearch products...")
        ingest_elasticsearch(
            products,
            args.reset,
            args.es_bulk_chunk_size,
            args.es_request_timeout,
            args.es_max_retries,
            skip_embeddings=args.skip_embeddings,
        )
        log("Ingesting Elasticsearch reviews...")
        ingest_elasticsearch_reviews(
            reviews,
            known_products,
            args.es_bulk_chunk_size,
            args.es_request_timeout,
            args.es_max_retries,
        )

    if args.engine in {"all", "meilisearch"}:
        log("Ingesting Meilisearch products...")
        ingest_meilisearch(products, args.reset, args.meili_chunk_size)
        log("Ingesting Meilisearch reviews...")
        ingest_meilisearch_reviews(reviews, known_products, args.meili_chunk_size)

    log_done("Ingest", started_at)
    log("Ingest complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())