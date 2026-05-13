from __future__ import annotations

from typing import Any

from elasticsearch import Elasticsearch

from backend.config import settings


class ElasticsearchSearchService:
    engine = "elasticsearch"

    def __init__(self) -> None:
        self.client = Elasticsearch(settings.elasticsearch_url, request_timeout=30)
        self.index = "products"

    def search(self, params: dict[str, Any]) -> dict[str, Any]:
        filters = []
        if params.get("brand"):
            filters.append({"term": {"brand.keyword": params["brand"]}})
        if params.get("category"):
            filters.append({"term": {"category.keyword": params["category"]}})
        if params.get("min_price") is not None or params.get("max_price") is not None:
            filters.append({"range": {"price": self._range(params, "price")}})
        if params.get("min_rating") is not None:
            filters.append({"range": {"rating": {"gte": params["min_rating"]}}})

        body = {
            "size": int(params.get("limit") or 10),
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": params["q"],
                                "fields": ["title^4", "brand^2", "category^2", "description", "review_text"],
                                "fuzziness": "AUTO",
                            }
                        }
                    ],
                    "filter": filters,
                }
            },
            "highlight": {"fields": {"title": {}, "description": {}, "review_text": {}}},
            "aggs": {
                "brands": {"terms": {"field": "brand.keyword", "size": 10}},
                "categories": {"terms": {"field": "category.keyword", "size": 10}},
                "price_ranges": {
                    "range": {
                        "field": "price",
                        "ranges": [
                            {"to": 50},
                            {"from": 50, "to": 100},
                            {"from": 100, "to": 300},
                            {"from": 300},
                        ],
                    }
                },
            },
        }
        response = self.client.search(index=self.index, body=body)
        hits = []
        for item in response["hits"]["hits"]:
            source = item["_source"]
            source["score"] = item["_score"]
            source["highlights"] = item.get("highlight", {})
            hits.append(source)

        return {
            "engine": self.engine,
            "hits": hits,
            "facets": self._facets(response.get("aggregations", {})),
            "total": response["hits"]["total"]["value"],
        }

    def review_analytics(self) -> dict[str, Any]:
        response = self.client.search(
            index=self.index,
            size=0,
            aggs={
                "avg_review_rating": {"avg": {"field": "avg_review_rating"}},
                "loaded_review_count": {"sum": {"field": "loaded_review_count"}},
                "helpful_votes": {"sum": {"field": "helpful_votes"}},
            },
        )
        aggs = response["aggregations"]
        return {
            "avg_rating": round(aggs["avg_review_rating"]["value"] or 0, 2),
            "review_count": int(aggs["loaded_review_count"]["value"] or 0),
            "helpful_votes": int(aggs["helpful_votes"]["value"] or 0),
        }

    def _range(self, params: dict[str, Any], name: str) -> dict[str, Any]:
        value: dict[str, Any] = {}
        if params.get(f"min_{name}") is not None:
            value["gte"] = params[f"min_{name}"]
        if params.get(f"max_{name}") is not None:
            value["lte"] = params[f"max_{name}"]
        return value

    def _facets(self, aggs: dict[str, Any]) -> dict[str, Any]:
        return {
            "brands": [{"value": b["key"], "count": b["doc_count"]} for b in aggs.get("brands", {}).get("buckets", [])],
            "categories": [{"value": b["key"], "count": b["doc_count"]} for b in aggs.get("categories", {}).get("buckets", [])],
            "price_ranges": aggs.get("price_ranges", {}).get("buckets", []),
        }
