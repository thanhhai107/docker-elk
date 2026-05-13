from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, FastAPI, Query

from backend.services.benchmark_service import BenchmarkService
from backend.services.elasticsearch_service import ElasticsearchSearchService
from backend.services.meilisearch_service import MeiliSearchService
from backend.services.postgres_service import PostgresSearchService


app = FastAPI(title="Amazon Electronics Search Demo")


def query_params(
    q: str = Query("wireless noise cancelling headphones"),
    brand: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    return {
        "q": q,
        "brand": brand or None,
        "category": category or None,
        "min_price": min_price,
        "max_price": max_price,
        "min_rating": min_rating,
        "limit": limit,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/search/{engine}")
def search(
    engine: Literal["elasticsearch", "meilisearch", "postgres"],
    params: dict[str, Any] = Depends(query_params),
) -> dict[str, Any]:
    services = {
        "elasticsearch": ElasticsearchSearchService(),
        "meilisearch": MeiliSearchService(),
        "postgres": PostgresSearchService(),
    }
    return services[engine].search(params)


@app.get("/compare")
def compare(params: dict[str, Any] = Depends(query_params)) -> dict[str, Any]:
    return BenchmarkService().compare(params)


@app.get("/analytics/reviews")
def review_analytics() -> dict[str, Any]:
    services = [ElasticsearchSearchService(), MeiliSearchService(), PostgresSearchService()]
    output = {}
    for service in services:
        try:
            output[service.engine] = service.review_analytics()
        except Exception as exc:
            output[service.engine] = {"error": str(exc)}
    return output
