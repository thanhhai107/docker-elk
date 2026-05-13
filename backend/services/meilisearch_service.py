from __future__ import annotations

from typing import Any

import meilisearch

from backend.config import settings


class MeiliSearchService:
    engine = "meilisearch"

    def __init__(self) -> None:
        self.client = meilisearch.Client(settings.meili_url, settings.meili_master_key)
        self.index = self.client.index("amazon_electronics_products")

    def search(self, params: dict[str, Any]) -> dict[str, Any]:
        filters = self._filters(params)
        options: dict[str, Any] = {
            "limit": int(params.get("limit") or 10),
            "attributesToHighlight": ["title", "description", "review_text"],
            "facets": ["brand", "category"],
            "showRankingScore": True,
        }
        if filters:
            options["filter"] = filters
        response = self.index.search(params["q"], options)
        hits = []
        for item in response.get("hits", []):
            formatted = item.pop("_formatted", {})
            item["score"] = item.pop("_rankingScore", None)
            item["highlights"] = {
                key: [value] for key, value in formatted.items() if isinstance(value, str)
            }
            hits.append(item)
        facet_distribution = response.get("facetDistribution", {})
        return {
            "engine": self.engine,
            "hits": hits,
            "facets": {
                "brands": self._facet_items(facet_distribution.get("brand", {})),
                "categories": self._facet_items(facet_distribution.get("category", {})),
            },
            "total": response.get("estimatedTotalHits", len(hits)),
        }

    def review_analytics(self) -> dict[str, Any]:
        response = self.index.search("", {"limit": 0, "facets": ["rating"]})
        stats = self.index.get_stats()
        return {
            "product_count": stats.number_of_documents,
            "note": "Meilisearch stores review aggregates on product documents in this demo.",
            "rating_facets": response.get("facetDistribution", {}).get("rating", {}),
        }

    def _filters(self, params: dict[str, Any]) -> list[str]:
        filters: list[str] = []
        if params.get("brand"):
            filters.append(f'brand = "{params["brand"]}"')
        if params.get("category"):
            filters.append(f'category = "{params["category"]}"')
        if params.get("min_price") is not None:
            filters.append(f'price >= {params["min_price"]}')
        if params.get("max_price") is not None:
            filters.append(f'price <= {params["max_price"]}')
        if params.get("min_rating") is not None:
            filters.append(f'rating >= {params["min_rating"]}')
        return filters

    def _facet_items(self, values: dict[str, int]) -> list[dict[str, Any]]:
        return [{"value": key, "count": count} for key, count in values.items()]
