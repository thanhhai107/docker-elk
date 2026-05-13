from __future__ import annotations

from pathlib import Path

from backend.ingest.normalize import iter_jsonl, normalize_product


def load_products(path: Path, limit: int | None = None) -> list[dict]:
    products = []
    for raw in iter_jsonl(path, limit):
        product = normalize_product(raw)
        if product:
            products.append(product)
    return products
