from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from backend.config import settings
from backend.ingest.pipeline import (
    enrich_products,
    enrich_reviews,
    format_limit,
    load_products_with_tracking,
    load_reviews_with_tracking,
    log,
    log_done,
    write_jsonl,
)


DEFAULT_MAX_REVIEWS_PER_PRODUCT = 5
DEFAULT_PRODUCT_LIMIT = 100_000
MANIFEST_VERSION = 1
PROCESSED_PRODUCTS_FILENAME = "products.jsonl"
PROCESSED_REVIEWS_FILENAME = "reviews.jsonl"
MANIFEST_FILENAME = "manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load and enrich products/reviews into processed JSONL files for ingest.",
    )
    parser.add_argument("--products", type=Path, default=None, help="Product JSONL/GZ source path.")
    parser.add_argument("--reviews", type=Path, default=None, help="Review JSONL/GZ source path.")
    parser.add_argument(
        "--product-limit",
        type=int,
        default=DEFAULT_PRODUCT_LIMIT,
        help=f"Max products to load. Default: {DEFAULT_PRODUCT_LIMIT}.",
    )
    parser.add_argument(
        "--max-reviews-per-product",
        type=int,
        default=DEFAULT_MAX_REVIEWS_PER_PRODUCT,
        help=(
            "Max reviews accepted per selected product. "
            f"Use 0 to disable this per-product cap. Default: {DEFAULT_MAX_REVIEWS_PER_PRODUCT}."
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Load every product and review from the source files (overrides --product-limit and --max-reviews-per-product).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write processed JSONL and manifest. Default: <data_dir>/processed.",
    )
    args = parser.parse_args()
    if args.all:
        args.product_limit = None
        args.max_reviews_per_product = 0
    return args


def default_path(*candidates: Path) -> Path:
    for path in candidates:
        if path.exists():
            return path
    return candidates[-1]


def resolve_output_dir(arg: Path | None) -> Path:
    if arg is not None:
        return arg
    return settings.data_dir / "processed"


def write_manifest(
    output_dir: Path,
    *,
    product_path: Path,
    review_path: Path,
    review_path_used: bool,
    product_limit: int | None,
    max_reviews_per_product: int | None,
    product_count: int,
    review_count: int,
) -> Path:
    manifest = {
        "version": MANIFEST_VERSION,
        "generated_at": int(time.time()),
        "product_source": str(product_path),
        "review_source": str(review_path) if review_path_used else None,
        "product_limit": product_limit,
        "max_reviews_per_product": max_reviews_per_product,
        "product_count": product_count,
        "review_count": review_count,
        "products_file": PROCESSED_PRODUCTS_FILENAME,
        "reviews_file": PROCESSED_REVIEWS_FILENAME,
    }
    manifest_path = output_dir / MANIFEST_FILENAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


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
    output_dir = resolve_output_dir(args.output_dir)
    per_product_cap = None if args.max_reviews_per_product <= 0 else args.max_reviews_per_product

    log("Starting prepare-data")
    log(f"Products source: {product_path}")
    log(f"Reviews source:  {review_path if review_path.exists() else 'not found'}")
    log(f"Output dir:      {output_dir}")
    log(f"Product limit:   {format_limit(args.product_limit)}")
    log(f"Max reviews per product: {format_limit(per_product_cap)}")

    log("Loading products")
    products = load_products_with_tracking(product_path, args.product_limit)
    log(f"Loaded {len(products)} products from source")
    known_products = {product['product_id'] for product in products}

    log("Loading reviews matching selected products")
    review_path_exists = review_path.exists()
    reviews, aggregates = load_reviews_with_tracking(
        review_path if review_path_exists else None,
        product_ids=known_products,
        max_reviews_per_product=per_product_cap,
    )
    log(f"Loaded {len(reviews)} reviews and {len(aggregates)} product review aggregates")

    log("Enriching products and reviews")
    enriched_products = enrich_products(products, aggregates)
    enriched_reviews = enrich_reviews(reviews, enriched_products)

    products_path = output_dir / PROCESSED_PRODUCTS_FILENAME
    reviews_path = output_dir / PROCESSED_REVIEWS_FILENAME
    log(f"Writing {len(enriched_products)} products to {products_path}")
    product_count = write_jsonl(products_path, enriched_products)
    log(f"Writing {len(enriched_reviews)} reviews to {reviews_path}")
    review_count = write_jsonl(reviews_path, enriched_reviews)

    manifest_path = write_manifest(
        output_dir,
        product_path=product_path,
        review_path=review_path,
        review_path_used=review_path_exists,
        product_limit=args.product_limit,
        max_reviews_per_product=per_product_cap,
        product_count=product_count,
        review_count=review_count,
    )
    log(f"Wrote manifest to {manifest_path}")
    log_done("Prepare-data", started_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())