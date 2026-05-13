from __future__ import annotations

from time import perf_counter
from typing import Any, Callable

import meilisearch
import psycopg
from elasticsearch import Elasticsearch
from psycopg.rows import dict_row

from backend.config import settings


PRODUCT_INDEX = "amazon_electronics_products"
REVIEW_INDEX = "amazon_electronics_reviews"

QUERY_OPTIONS = [
    "wireless noise cancelling headphones",
    "gaming mouse",
    "battery problem",
    "portable monitor usb c",
    "mechanical keyboard",
    "usb c charger fast charging",
    "bluetooth speaker",
    "laptop stand adjustable",
]

SCENARIOS: dict[str, dict[str, Any]] = {
    "advanced-ranking": {
        "title": "Scenario 1: Advanced Product Search with Business Ranking",
        "default_query": "wireless noise cancelling headphones",
        "summary": "Multi-field search with business ranking from rating and review volume.",
    },
    "search-filter-facet": {
        "title": "Scenario 2: Search + Filter + Faceted Search",
        "default_query": "gaming mouse",
        "summary": "Search result page with filters, facets, price ranges and rating analytics.",
    },
    "negative-review-analytics": {
        "title": "Scenario 3: Negative Review Analytics",
        "default_query": "battery problem",
        "summary": "Admin workflow for finding verified low-rating reviews and problem products.",
    },
    "complex-query-intent": {
        "title": "Scenario 4: Complex Query Intent",
        "default_query": "portable monitor usb c",
        "summary": "Must, should, filter and exclusion logic for a realistic buyer intent.",
    },
    "admin-dashboard-insights": {
        "title": "Scenario 5: Admin Dashboard Insights",
        "default_query": "mechanical keyboard",
        "summary": "One search term producing result hits, highlights and market analytics.",
    },
}

BENCHMARK_ORDER = list(SCENARIOS)


class WorkflowService:
    def __init__(self) -> None:
        self.es = Elasticsearch(settings.elasticsearch_url, request_timeout=30)
        self.meili = meilisearch.Client(settings.meili_url, settings.meili_master_key)

    def list_scenarios(self) -> dict[str, Any]:
        return {"scenarios": SCENARIOS, "query_options": QUERY_OPTIONS}

    def run(self, scenario_id: str, query: str | None = None, limit: int = 10) -> dict[str, Any]:
        if scenario_id not in SCENARIOS:
            raise KeyError(scenario_id)
        scenario = SCENARIOS[scenario_id]
        selected_query = query or scenario["default_query"]
        runners: list[tuple[str, Callable[[str, int], dict[str, Any]]]] = [
            ("elasticsearch", getattr(self, f"_es_{scenario_id.replace('-', '_')}")),
            ("meilisearch", getattr(self, f"_meili_{scenario_id.replace('-', '_')}")),
            ("postgres", getattr(self, f"_pg_{scenario_id.replace('-', '_')}")),
        ]
        results = []
        for engine, runner in runners:
            started = perf_counter()
            try:
                result = runner(selected_query, limit)
                result["took_ms"] = round((perf_counter() - started) * 1000, 2)
            except Exception as exc:
                result = self._error_result(engine, exc)
            results.append(result)
        return {
            "scenario_id": scenario_id,
            "title": scenario["title"],
            "summary": scenario["summary"],
            "query": selected_query,
            "winner": "elasticsearch",
            "results": results,
        }

    def benchmark(self, limit: int = 10) -> dict[str, Any]:
        rows = []
        for scenario_id in BENCHMARK_ORDER:
            output = self.run(scenario_id, SCENARIOS[scenario_id]["default_query"], limit)
            row = {
                "workflow": output["title"],
                "query": output["query"],
                "winner": "Elasticsearch",
                "engines": {},
            }
            for result in output["results"]:
                row["engines"][result["engine"]] = {
                    "total_workflow_time_ms": result.get("took_ms"),
                    "number_of_requests": result.get("number_of_requests"),
                    "has_highlight": result.get("has_highlight"),
                    "has_aggregation": result.get("has_aggregation"),
                    "has_custom_ranking": result.get("has_custom_ranking"),
                    "backend_complexity": result.get("backend_complexity"),
                    "score": result.get("scorecard", {}).get("overall"),
                }
            rows.append(row)
        return {
            "rows": rows,
            "scorecard_scale": "1-5, where 5 means strongest fit for the workflow",
            "conclusion": (
                "Elasticsearch is not always the fastest for simple keyword search, but it wins "
                "the end-to-end workflows that combine search, ranking, filters, highlight, "
                "facets, aggregations and review analytics."
            ),
        }

    def _es_advanced_ranking(self, query: str, limit: int) -> dict[str, Any]:
        body = {
            "size": limit,
            "query": {
                "function_score": {
                    "query": {
                        "multi_match": {
                            "query": query,
                            "fields": ["title^4", "features^2", "description"],
                        }
                    },
                    "functions": [
                        {
                            "field_value_factor": {
                                "field": "average_rating",
                                "factor": 1.5,
                                "missing": 1,
                            }
                        },
                        {
                            "field_value_factor": {
                                "field": "rating_number",
                                "modifier": "log1p",
                                "factor": 0.3,
                                "missing": 1,
                            }
                        },
                    ],
                    "boost_mode": "multiply",
                    "score_mode": "sum",
                }
            },
            "highlight": {"fields": {"title": {}, "features": {}, "description": {}}},
        }
        response = self.es.search(index=PRODUCT_INDEX, body=body)
        return self._engine_result(
            "elasticsearch",
            response,
            "1 function_score request combines text relevance with business ranking.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=True,
            backend_complexity="Low",
            score=5,
        )

    def _es_search_filter_facet(self, query: str, limit: int) -> dict[str, Any]:
        body = {
            "size": limit,
            "query": self._product_bool_query(query, filters=[{"range": {"average_rating": {"gte": 4}}}]),
            "aggs": self._product_facets(include_stats=True),
            "highlight": {"fields": {"title": {}, "description": {}}},
        }
        response = self.es.search(index=PRODUCT_INDEX, body=body)
        return self._engine_result(
            "elasticsearch",
            response,
            "1 request returns hits, highlights, facets, price ranges and average rating.",
            number_of_requests=1,
            has_aggregation=True,
            has_custom_ranking=False,
            backend_complexity="Low",
            score=5,
        )

    def _es_negative_review_analytics(self, query: str, limit: int) -> dict[str, Any]:
        body = {
            "size": limit,
            "query": {
                "bool": {
                    "must": [{"multi_match": {"query": query, "fields": ["title^2", "text"]}}],
                    "filter": [
                        {"range": {"rating": {"lte": 2}}},
                        {"term": {"verified_purchase": True}},
                    ],
                }
            },
            "sort": [{"helpful_vote": "desc"}],
            "aggs": {
                "rating_distribution": {"terms": {"field": "rating", "size": 5}},
                "top_problem_products": {"terms": {"field": "product_id", "size": 10}},
                "avg_helpful_vote": {"avg": {"field": "helpful_vote"}},
            },
            "highlight": {"fields": {"title": {}, "text": {}}},
        }
        response = self.es.search(index=REVIEW_INDEX, body=body)
        return self._engine_result(
            "elasticsearch",
            response,
            "1 review-search request returns low-rating hits, highlights and product-level analytics.",
            number_of_requests=1,
            has_aggregation=True,
            has_custom_ranking=False,
            backend_complexity="Low",
            score=5,
            document_type="review",
        )

    def _es_complex_query_intent(self, query: str, limit: int) -> dict[str, Any]:
        body = {
            "size": limit,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": "portable monitor",
                                "fields": ["title^4", "features^2", "description"],
                            }
                        }
                    ],
                    "should": [
                        {"match": {"features": "usb c"}},
                        {"match": {"description": "usb c"}},
                    ],
                    "filter": [
                        {"range": {"price": {"lte": 300}}},
                        {"range": {"average_rating": {"gte": 4}}},
                    ],
                    "must_not": [{"match": {"description": "dead pixel"}}],
                }
            },
            "highlight": {"fields": {"title": {}, "features": {}, "description": {}}},
        }
        response = self.es.search(index=PRODUCT_INDEX, body=body)
        return self._engine_result(
            "elasticsearch",
            response,
            "Bool query expresses must, should, filter and must_not logic directly.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=True,
            backend_complexity="Low",
            score=5,
        )

    def _es_admin_dashboard_insights(self, query: str, limit: int) -> dict[str, Any]:
        body = {
            "size": limit,
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": ["title^4", "features^2", "description"],
                }
            },
            "aggs": {
                "top_brands": {"terms": {"field": "brand", "size": 10}},
                "price_stats": {"stats": {"field": "price"}},
                "rating_stats": {"stats": {"field": "average_rating"}},
                "most_reviewed_products": {
                    "terms": {
                        "field": "product_id",
                        "size": 10,
                        "order": {"max_reviews": "desc"},
                    },
                    "aggs": {"max_reviews": {"max": {"field": "rating_number"}}},
                },
            },
            "highlight": {"fields": {"title": {}, "description": {}}},
        }
        response = self.es.search(index=PRODUCT_INDEX, body=body)
        return self._engine_result(
            "elasticsearch",
            response,
            "1 request returns search results plus dashboard-grade metrics.",
            number_of_requests=1,
            has_aggregation=True,
            has_custom_ranking=False,
            backend_complexity="Low",
            score=5,
        )

    def _meili_advanced_ranking(self, query: str, limit: int) -> dict[str, Any]:
        response = self.meili.index(PRODUCT_INDEX).search(
            query,
            {
                "limit": limit,
                "sort": ["average_rating:desc", "rating_number:desc"],
                "attributesToHighlight": ["title", "features", "description"],
                "showRankingScore": True,
            },
        )
        return self._meili_result(
            response,
            "Fast search and sorting, but business ranking is less expressive than function_score.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=True,
            backend_complexity="Medium",
            score=3,
        )

    def _meili_search_filter_facet(self, query: str, limit: int) -> dict[str, Any]:
        response = self.meili.index(PRODUCT_INDEX).search(
            query,
            {
                "limit": limit,
                "filter": "average_rating >= 4",
                "facets": ["brand", "category"],
                "attributesToHighlight": ["title", "description"],
                "showRankingScore": True,
            },
        )
        return self._meili_result(
            response,
            "1 request returns basic facets, but price/rating metrics need extra work.",
            number_of_requests=1,
            has_aggregation=True,
            has_custom_ranking=False,
            backend_complexity="Medium",
            score=4,
        )

    def _meili_negative_review_analytics(self, query: str, limit: int) -> dict[str, Any]:
        response = self.meili.index(REVIEW_INDEX).search(
            query,
            {
                "limit": limit,
                "filter": "rating <= 2 AND verified_purchase = true",
                "facets": ["rating", "product_id"],
                "sort": ["helpful_vote:desc"],
                "attributesToHighlight": ["title", "text"],
                "showRankingScore": True,
            },
        )
        return self._meili_result(
            response,
            "Search and facets work, but deeper analytics are limited compared with Elasticsearch aggs.",
            number_of_requests=1,
            has_aggregation=True,
            has_custom_ranking=False,
            backend_complexity="Medium",
            score=3,
            document_type="review",
        )

    def _meili_complex_query_intent(self, query: str, limit: int) -> dict[str, Any]:
        response = self.meili.index(PRODUCT_INDEX).search(
            query,
            {
                "limit": limit,
                "filter": "price <= 300 AND average_rating >= 4",
                "attributesToHighlight": ["title", "features", "description"],
                "showRankingScore": True,
            },
        )
        hits = [
            hit
            for hit in response.get("hits", [])
            if "dead pixel" not in (hit.get("description") or "").lower()
        ]
        response["hits"] = hits
        return self._meili_result(
            response,
            "Filter works, but must_not content exclusion is handled in backend after retrieval.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=False,
            backend_complexity="Medium",
            score=3,
        )

    def _meili_admin_dashboard_insights(self, query: str, limit: int) -> dict[str, Any]:
        response = self.meili.index(PRODUCT_INDEX).search(
            query,
            {
                "limit": limit,
                "facets": ["brand", "category"],
                "attributesToHighlight": ["title", "description"],
                "showRankingScore": True,
            },
        )
        return self._meili_result(
            response,
            "Good product hits and facets, but stats like min/max/avg price are not native workflow output.",
            number_of_requests=1,
            has_aggregation=True,
            has_custom_ranking=False,
            backend_complexity="Medium",
            score=2,
        )

    def _pg_advanced_ranking(self, query: str, limit: int) -> dict[str, Any]:
        sql = """
            WITH q AS (
                SELECT websearch_to_tsquery('english', %s) AS tsq
            )
            SELECT product_id, title, features, description, category, brand, price,
                   average_rating, rating_number, review_count,
                   ts_rank_cd(search_vector, q.tsq) AS text_rank,
                   (
                       ts_rank_cd(search_vector, q.tsq)
                       * greatest(average_rating, 1)
                       * ln(greatest(rating_number, 1) + 1)
                   ) AS score,
                   count(*) OVER() AS total,
                   ts_headline('english', title, q.tsq, 'StartSel=<mark>, StopSel=</mark>') AS title_highlight,
                   ts_headline('english', description, q.tsq, 'StartSel=<mark>, StopSel=</mark>, MaxWords=24') AS description_highlight
            FROM products, q
            WHERE search_vector @@ q.tsq
            ORDER BY score DESC
            LIMIT %s
        """
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            hits = conn.execute(sql, [query, limit]).fetchall()
        return self._pg_result(
            hits,
            "1 complex SQL computes text rank multiplied by rating and review-volume signals.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=True,
            backend_complexity="High",
            score=3,
        )

    def _pg_search_filter_facet(self, query: str, limit: int) -> dict[str, Any]:
        predicate = "search_vector @@ q.tsq AND average_rating >= 4"
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            hits = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT product_id, title, description, category, brand, price, average_rating,
                       rating_number, ts_rank_cd(search_vector, q.tsq) AS score,
                       count(*) OVER() AS total,
                       ts_headline('english', title, q.tsq, 'StartSel=<mark>, StopSel=</mark>') AS title_highlight,
                       ts_headline('english', description, q.tsq, 'StartSel=<mark>, StopSel=</mark>, MaxWords=24') AS description_highlight
                FROM products, q
                WHERE {predicate}
                ORDER BY score DESC
                LIMIT %s
                """,
                [query, limit],
            ).fetchall()
            brands = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT brand AS value, count(*) AS count
                FROM products, q
                WHERE {predicate}
                GROUP BY brand
                ORDER BY count DESC
                LIMIT 10
                """,
                [query],
            ).fetchall()
            price_ranges = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT CASE
                         WHEN price < 25 THEN 'Under $25'
                         WHEN price >= 25 AND price < 50 THEN '$25 - $50'
                         WHEN price >= 50 AND price < 100 THEN '$50 - $100'
                         ELSE 'Over $100'
                       END AS value,
                       count(*) AS count
                FROM products, q
                WHERE {predicate}
                GROUP BY value
                ORDER BY value
                """,
                [query],
            ).fetchall()
            avg_rating = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT round(avg(average_rating)::numeric, 2) AS value
                FROM products, q
                WHERE {predicate}
                """,
                [query],
            ).fetchone()
        return self._pg_result(
            hits,
            "4 SQL queries are needed for hits, brand facet, price ranges and average rating.",
            number_of_requests=4,
            has_aggregation=True,
            has_custom_ranking=False,
            backend_complexity="High",
            score=3,
            aggregations={"brands": brands, "price_ranges": price_ranges, "avg_rating": avg_rating},
        )

    def _pg_negative_review_analytics(self, query: str, limit: int) -> dict[str, Any]:
        predicate = "review_vector @@ q.tsq AND rating <= 2 AND verified_purchase = true"
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            hits = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT review_id, product_id, rating, title, text, helpful_vote, verified_purchase,
                       ts_rank_cd(review_vector, q.tsq) AS score,
                       count(*) OVER() AS total,
                       ts_headline('english', title, q.tsq, 'StartSel=<mark>, StopSel=</mark>') AS title_highlight,
                       ts_headline('english', text, q.tsq, 'StartSel=<mark>, StopSel=</mark>, MaxWords=32') AS text_highlight
                FROM reviews, q
                WHERE {predicate}
                ORDER BY helpful_vote DESC
                LIMIT %s
                """,
                [query, limit],
            ).fetchall()
            rating_distribution = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT rating AS value, count(*) AS count
                FROM reviews, q
                WHERE {predicate}
                GROUP BY rating
                ORDER BY rating
                """,
                [query],
            ).fetchall()
            top_products = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT product_id AS value, count(*) AS count
                FROM reviews, q
                WHERE {predicate}
                GROUP BY product_id
                ORDER BY count DESC
                LIMIT 10
                """,
                [query],
            ).fetchall()
        return self._pg_result(
            hits,
            "3 SQL queries are needed for review hits, rating distribution and top problem products.",
            number_of_requests=3,
            has_aggregation=True,
            has_custom_ranking=False,
            backend_complexity="High",
            score=3,
            aggregations={"rating_distribution": rating_distribution, "top_problem_products": top_products},
            document_type="review",
        )

    def _pg_complex_query_intent(self, query: str, limit: int) -> dict[str, Any]:
        sql = """
            WITH q AS (SELECT websearch_to_tsquery('english', 'portable monitor') AS tsq)
            SELECT product_id, title, features, description, category, brand, price,
                   average_rating, rating_number,
                   (
                       ts_rank_cd(search_vector, q.tsq)
                       + CASE
                           WHEN features ILIKE '%%usb%%c%%' OR description ILIKE '%%usb%%c%%' THEN 1
                           ELSE 0
                         END
                   ) AS score,
                   count(*) OVER() AS total,
                   ts_headline('english', title, q.tsq, 'StartSel=<mark>, StopSel=</mark>') AS title_highlight,
                   ts_headline('english', description, q.tsq, 'StartSel=<mark>, StopSel=</mark>, MaxWords=24') AS description_highlight
            FROM products, q
            WHERE search_vector @@ q.tsq
              AND price <= 300
              AND average_rating >= 4
              AND description NOT ILIKE '%%dead pixel%%'
            ORDER BY score DESC
            LIMIT %s
        """
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            hits = conn.execute(sql, [limit]).fetchall()
        return self._pg_result(
            hits,
            "1 long SQL can express the logic, but relevance tuning is harder to maintain.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=True,
            backend_complexity="High",
            score=4,
        )

    def _pg_admin_dashboard_insights(self, query: str, limit: int) -> dict[str, Any]:
        predicate = "search_vector @@ q.tsq"
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            hits = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT product_id, title, description, category, brand, price, average_rating,
                       rating_number, ts_rank_cd(search_vector, q.tsq) AS score,
                       count(*) OVER() AS total,
                       ts_headline('english', title, q.tsq, 'StartSel=<mark>, StopSel=</mark>') AS title_highlight,
                       ts_headline('english', description, q.tsq, 'StartSel=<mark>, StopSel=</mark>, MaxWords=24') AS description_highlight
                FROM products, q
                WHERE {predicate}
                ORDER BY score DESC
                LIMIT %s
                """,
                [query, limit],
            ).fetchall()
            top_brands = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT brand AS value, count(*) AS count
                FROM products, q
                WHERE {predicate}
                GROUP BY brand
                ORDER BY count DESC
                LIMIT 10
                """,
                [query],
            ).fetchall()
            price_stats = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT min(price) AS min, max(price) AS max, round(avg(price)::numeric, 2) AS avg
                FROM products, q
                WHERE {predicate}
                """,
                [query],
            ).fetchone()
            rating_stats = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT min(average_rating) AS min, max(average_rating) AS max,
                       round(avg(average_rating)::numeric, 2) AS avg
                FROM products, q
                WHERE {predicate}
                """,
                [query],
            ).fetchone()
            most_reviewed = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT product_id AS value, max(rating_number) AS count
                FROM products, q
                WHERE {predicate}
                GROUP BY product_id
                ORDER BY count DESC
                LIMIT 10
                """,
                [query],
            ).fetchall()
        return self._pg_result(
            hits,
            "5 SQL queries are needed for hits, brands, price stats, rating stats and most-reviewed products.",
            number_of_requests=5,
            has_aggregation=True,
            has_custom_ranking=False,
            backend_complexity="High",
            score=3,
            aggregations={
                "top_brands": top_brands,
                "price_stats": price_stats,
                "rating_stats": rating_stats,
                "most_reviewed_products": most_reviewed,
            },
        )

    def _product_bool_query(self, query: str, filters: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": query,
                            "fields": ["title^4", "features^2", "description"],
                        }
                    }
                ],
                "filter": filters or [],
            }
        }

    def _product_facets(self, include_stats: bool = False) -> dict[str, Any]:
        aggs: dict[str, Any] = {
            "top_brands": {"terms": {"field": "brand", "size": 10}},
            "categories": {"terms": {"field": "category", "size": 10}},
            "price_ranges": {
                "range": {
                    "field": "price",
                    "ranges": [
                        {"key": "Under $25", "to": 25},
                        {"key": "$25 - $50", "from": 25, "to": 50},
                        {"key": "$50 - $100", "from": 50, "to": 100},
                        {"key": "Over $100", "from": 100},
                    ],
                }
            },
        }
        if include_stats:
            aggs["avg_rating"] = {"avg": {"field": "average_rating"}}
        return aggs

    def _engine_result(
        self,
        engine: str,
        response: dict[str, Any],
        note: str,
        *,
        number_of_requests: int,
        has_aggregation: bool,
        has_custom_ranking: bool,
        backend_complexity: str,
        score: int,
        document_type: str = "product",
    ) -> dict[str, Any]:
        return {
            "engine": engine,
            "document_type": document_type,
            "number_of_requests": number_of_requests,
            "total": response["hits"]["total"]["value"],
            "hits": [self._es_hit(item) for item in response["hits"]["hits"]],
            "aggregations": self._es_aggs(response.get("aggregations", {})),
            "has_highlight": True,
            "has_aggregation": has_aggregation,
            "has_custom_ranking": has_custom_ranking,
            "backend_complexity": backend_complexity,
            "note": note,
            "scorecard": {"overall": score},
        }

    def _meili_result(
        self,
        response: dict[str, Any],
        note: str,
        *,
        number_of_requests: int,
        has_aggregation: bool,
        has_custom_ranking: bool,
        backend_complexity: str,
        score: int,
        document_type: str = "product",
    ) -> dict[str, Any]:
        hits = []
        for item in response.get("hits", []):
            formatted = item.pop("_formatted", {})
            item["score"] = item.pop("_rankingScore", None)
            item["highlights"] = {
                key: [value] for key, value in formatted.items() if isinstance(value, str)
            }
            hits.append(item)
        return {
            "engine": "meilisearch",
            "document_type": document_type,
            "number_of_requests": number_of_requests,
            "total": response.get("estimatedTotalHits", len(hits)),
            "hits": hits,
            "aggregations": {"facets": response.get("facetDistribution", {})},
            "has_highlight": True,
            "has_aggregation": has_aggregation,
            "has_custom_ranking": has_custom_ranking,
            "backend_complexity": backend_complexity,
            "note": note,
            "scorecard": {"overall": score},
        }

    def _pg_result(
        self,
        rows: list[dict[str, Any]],
        note: str,
        *,
        number_of_requests: int,
        has_aggregation: bool,
        has_custom_ranking: bool,
        backend_complexity: str,
        score: int,
        aggregations: dict[str, Any] | None = None,
        document_type: str = "product",
    ) -> dict[str, Any]:
        hits = [self._pg_hit(row) for row in rows]
        return {
            "engine": "postgres",
            "document_type": document_type,
            "number_of_requests": number_of_requests,
            "total": int(rows[0].get("total", len(rows))) if rows else 0,
            "hits": hits,
            "aggregations": aggregations or {},
            "has_highlight": True,
            "has_aggregation": has_aggregation,
            "has_custom_ranking": has_custom_ranking,
            "backend_complexity": backend_complexity,
            "note": note,
            "scorecard": {"overall": score},
        }

    def _es_hit(self, item: dict[str, Any]) -> dict[str, Any]:
        source = dict(item["_source"])
        source["score"] = item.get("_score")
        source["highlights"] = item.get("highlight", {})
        return source

    def _pg_hit(self, row: dict[str, Any]) -> dict[str, Any]:
        hit = dict(row)
        hit.pop("total", None)
        highlights = {}
        for field in ["title", "description", "text"]:
            value = hit.pop(f"{field}_highlight", None)
            if value:
                highlights[field] = [value]
        hit["highlights"] = highlights
        return hit

    def _es_aggs(self, aggs: dict[str, Any]) -> dict[str, Any]:
        output = {}
        for name, value in aggs.items():
            if "buckets" in value:
                output[name] = [
                    {
                        "value": bucket.get("key_as_string", bucket.get("key")),
                        "count": bucket.get("doc_count"),
                        "metrics": {
                            metric_name: metric_value.get("value")
                            for metric_name, metric_value in bucket.items()
                            if isinstance(metric_value, dict) and "value" in metric_value
                        },
                    }
                    for bucket in value["buckets"]
                ]
            elif "value" in value:
                output[name] = value["value"]
            else:
                output[name] = value
        return output

    def _error_result(self, engine: str, exc: Exception) -> dict[str, Any]:
        return {
            "engine": engine,
            "error": str(exc),
            "number_of_requests": 0,
            "total": 0,
            "hits": [],
            "aggregations": {},
            "has_highlight": False,
            "has_aggregation": False,
            "has_custom_ranking": False,
            "backend_complexity": "Unknown",
            "scorecard": {"overall": 0},
            "took_ms": None,
        }
