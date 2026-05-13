from __future__ import annotations

from typing import Any

from backend.services.elasticsearch_service import ElasticsearchSearchService
from backend.services.meilisearch_service import MeiliSearchService
from backend.services.postgres_service import PostgresSearchService
from backend.utils.timer import timed


class BenchmarkService:
    def __init__(self) -> None:
        self.services = [
            ElasticsearchSearchService(),
            MeiliSearchService(),
            PostgresSearchService(),
        ]

    def compare(self, params: dict[str, Any]) -> dict[str, Any]:
        results = []
        for service in self.services:
            try:
                result, took_ms = timed(lambda service=service: service.search(params))
                result["took_ms"] = took_ms
            except Exception as exc:
                result = {
                    "engine": service.engine,
                    "error": str(exc),
                    "hits": [],
                    "facets": {},
                    "total": 0,
                    "took_ms": None,
                }
            results.append(result)
        return {"query": params["q"], "results": results}
