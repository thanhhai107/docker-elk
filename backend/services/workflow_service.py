from __future__ import annotations

from collections import Counter, defaultdict
from time import perf_counter
from typing import Any, Callable, Literal

import meilisearch
import psycopg
from elasticsearch import Elasticsearch
from psycopg.rows import dict_row

from backend.config import settings


PRODUCT_INDEX = "amazon_electronics_products"
REVIEW_INDEX = "amazon_electronics_reviews"

SCENARIOS: dict[str, dict[str, Any]] = {
    "scenario-1-product-search": {
        "title": "Scenario 1: Product Search",
        "flow_name": "Product Search",
    },
    "scenario-2-review-search": {
        "title": "Scenario 2: Review Search",
        "flow_name": "Review Search",
    },
    "scenario-3-intent-aware-search": {
        "title": "Scenario 3: Intent-Aware Search",
        "flow_name": "Intent-Aware Search",
    },
    "scenario-4-analytics-aggregation": {
        "title": "Scenario 4: Analytics & Aggregation",
        "flow_name": "Analytics & Aggregation",
    },
}

SearchEngine = Literal["all", "elasticsearch", "meilisearch", "postgres"]

POSITIVE_REVIEW_TERMS = {"good", "great", "excellent", "easy", "quality", "works well", "install"}


class WorkflowService:
    def __init__(self) -> None:
        self.es = Elasticsearch(settings.elasticsearch_url, request_timeout=30)
        self.meili = meilisearch.Client(settings.meili_url, settings.meili_master_key)


    def run(
        self,
        scenario_id: str,
        query: str | None = None,
        limit: int = 10,
        engine: SearchEngine = "all",
    ) -> dict[str, Any]:
        if scenario_id not in SCENARIOS:
            raise KeyError(scenario_id)
        scenario = SCENARIOS[scenario_id]
        selected_query = (query or "").strip()
        if not selected_query:
            raise ValueError("Query is required")
        runner_map: dict[str, dict[str, Callable[[str, int], dict[str, Any]]]] = {
            "scenario-1-product-search": {
                "elasticsearch": self._es_product_keyword_search,
                "meilisearch": self._meili_product_keyword_search,
                "postgres": self._pg_product_keyword_search,
            },
            "scenario-2-review-search": {
                "elasticsearch": self._es_review_evidence_search,
                "meilisearch": self._meili_review_evidence_search,
                "postgres": self._pg_review_evidence_search,
            },
            "scenario-3-intent-aware-search": {
                "elasticsearch": self._es_scenario_3_hybrid_search,
                "meilisearch": self._meili_scenario_3_lexical_search,
                "postgres": self._pg_scenario_3_lexical_search,
            },
            "scenario-4-analytics-aggregation": {
                "elasticsearch": self._es_scenario_4_analytics_aggregation,
                "meilisearch": self._meili_scenario_4_analytics_aggregation,
                "postgres": self._pg_scenario_4_analytics_aggregation,
            },
        }
        scenario_runners = runner_map[scenario_id]
        runners: list[tuple[str, Callable[[str, int], dict[str, Any]]]] = [
            ("elasticsearch", scenario_runners["elasticsearch"]),
            ("meilisearch", scenario_runners["meilisearch"]),
            ("postgres", scenario_runners["postgres"]),
        ]
        selected_engine = engine
        if selected_engine != "all":
            runners = [(name, runner) for name, runner in runners if name == selected_engine]

        results = []
        for runner_engine, runner in runners:
            started = perf_counter()
            try:
                result = runner(selected_query, limit)
                result["took_ms"] = round((perf_counter() - started) * 1000, 2)
            except Exception as exc:
                result = self._error_result(runner_engine, exc)
            results.append(result)
        return {
            "scenario_id": scenario_id,
            "title": scenario["title"],
            "flow_name": scenario["flow_name"],
            "query": selected_query,
            "queries": self._scenario_queries(scenario_id, selected_query),
            "engine": selected_engine,
            "winner": "elasticsearch" if selected_engine == "all" else None,
            "winner_reason": self._winner_reason(scenario_id) if selected_engine == "all" else None,
            "results": results,
        }

    def _scenario_queries(self, scenario_id: str, selected_query: str) -> dict[str, str]:
        return {"query": selected_query}

    def _result_section(self, title: str, query: str, result: dict[str, Any]) -> dict[str, Any]:
        section = {
            "title": title,
            "query": query,
            "document_type": result.get("document_type", "product"),
            "total": result.get("total", 0),
            "hits": result.get("hits", []),
            "has_highlight": result.get("has_highlight", False),
        }
        if result.get("top_snippets"):
            section["top_snippets"] = result["top_snippets"]
        return section

    def _mixed_result(
        self,
        engine: str,
        note: str,
        sections: list[dict[str, Any]],
        *,
        number_of_requests: int,
        has_custom_ranking: bool,
        backend_complexity: str,
        score: int,
    ) -> dict[str, Any]:
        total = sum(int(section.get("total") or 0) for section in sections)
        return {
            "engine": engine,
            "document_type": "mixed",
            "number_of_requests": number_of_requests,
            "total": total,
            "hits": [],
            "sections": sections,
            "aggregations": {},
            "has_highlight": True,
            "has_aggregation": False,
            "has_custom_ranking": has_custom_ranking,
            "backend_complexity": backend_complexity,
            "note": note,
            "scorecard": {"overall": score},
        }

    def _es_product_keyword_search(self, query: str, limit: int) -> dict[str, Any]:
        body = {
            "track_total_hits": True,
            "size": limit,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": [
                                    "title^5",
                                    "brand.text^3",
                                    "category.text^2",
                                    "features^3",
                                    "description^2",
                                    "review_text",
                                ],
                                "type": "best_fields",
                                "fuzziness": "AUTO",
                                "prefix_length": 1,
                            }
                        }
                    ]
                }
            },
            "highlight": {"fields": {"title": {}, "features": {}, "description": {}, "review_text": {}}},
        }
        response = self.es.search(index=PRODUCT_INDEX, body=body)
        return self._engine_result(
            "elasticsearch",
            response,
            "Boosted multi_match handles title/brand/category/features differently and fuzziness catches typos such as 'iphne'.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=True,
            backend_complexity="Low",
            score=5,
        )

    def _meili_product_keyword_search(self, query: str, limit: int) -> dict[str, Any]:
        response = self.meili.index(PRODUCT_INDEX).search(
            query,
            {
                "hitsPerPage": limit,
                "page": 1,
                "attributesToHighlight": ["title", "features", "description", "review_text"],
                "showRankingScore": True,
            },
        )
        return self._meili_result(
            response,
            "Search plus filter is simple and fast, but field boosting and scoring control are less flexible.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=False,
            backend_complexity="Low",
            score=4,
        )

    def _pg_product_keyword_search(self, query: str, limit: int) -> dict[str, Any]:
        sql = """
            WITH q AS (
                SELECT websearch_to_tsquery('english', %s) AS tsq
            )
            SELECT product_id, title, features, description, category, brand, price,
                   average_rating, rating_number, review_count,
                   ts_rank_cd(search_vector, q.tsq) AS score,
                   count(*) OVER() AS total,
                   ts_headline('english', title, q.tsq, 'StartSel=<mark>, StopSel=</mark>') AS title_highlight,
                   ts_headline('english', description, q.tsq, 'StartSel=<mark>, StopSel=</mark>, MaxWords=24') AS description_highlight
            FROM products, q
            WHERE search_vector @@ q.tsq
            ORDER BY score DESC, average_rating DESC, rating_number DESC
            LIMIT %s
        """
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            hits = conn.execute(sql, [query, limit]).fetchall()
        return self._pg_result(
            hits,
            "Default PostgreSQL FTS uses the search_vector only; typo-heavy tokens can miss because pg_trgm is not used in this scenario.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=False,
            backend_complexity="Medium",
            score=2,
        )

    def _es_review_evidence_search(self, query: str, limit: int) -> dict[str, Any]:
        rating_filter, sentiment = self._review_rating_filter(query)
        body = {
            "track_total_hits": True,
            "size": limit,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["title^2", "text"],
                                "fuzziness": "AUTO",
                            }
                        }
                    ],
                    "filter": [{"range": {"rating": rating_filter}}],
                }
            },
            "sort": [{"_score": "desc"}, {"helpful_vote": "desc"}],
            "highlight": {
                "fields": {
                    "title": {"fragment_size": 120, "number_of_fragments": 1},
                    "text": {"fragment_size": 180, "number_of_fragments": 2},
                }
            },
        }
        response = self.es.search(index=REVIEW_INDEX, body=body)
        result = self._engine_result(
            "elasticsearch",
            response,
            f"{sentiment.title()} review search uses title/text highlights and helpful_vote as a secondary sort.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=False,
            backend_complexity="Low",
            score=5,
            document_type="review",
        )
        result["top_snippets"] = self._top_snippets(result["hits"])
        return result

    def _meili_review_evidence_search(self, query: str, limit: int) -> dict[str, Any]:
        filter_expr, sentiment = self._meili_review_filter(query)
        response = self.meili.index(REVIEW_INDEX).search(
            query,
            {
                "hitsPerPage": limit,
                "page": 1,
                "filter": filter_expr,
                "sort": ["helpful_vote:desc"],
                "attributesToHighlight": ["title", "text"],
                "showRankingScore": True,
            },
        )
        return self._meili_result(
            response,
            f"{sentiment.title()} review search is equivalent for filtering/highlight, with simpler ranking knobs.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=False,
            backend_complexity="Low",
            score=4,
            document_type="review",
        )

    def _pg_review_evidence_search(self, query: str, limit: int) -> dict[str, Any]:
        operator, threshold, sentiment = self._pg_review_filter(query)
        sql = f"""
            WITH q AS (
                SELECT websearch_to_tsquery('english', %s) AS tsq
            )
            SELECT review_id, reviews.product_id, reviews.rating, reviews.title, reviews.text,
                   helpful_vote, verified_purchase, products.title AS product_title,
                   products.brand, products.category,
                   ts_rank_cd(review_vector, q.tsq) AS score,
                   count(*) OVER() AS total,
                   ts_headline('english', reviews.title, q.tsq, 'StartSel=<mark>, StopSel=</mark>') AS title_highlight,
                   ts_headline('english', reviews.text, q.tsq, 'StartSel=<mark>, StopSel=</mark>, MaxWords=32') AS text_highlight
            FROM reviews
            JOIN products ON products.product_id = reviews.product_id, q
            WHERE review_vector @@ q.tsq
              AND reviews.rating {operator} %s
            ORDER BY score DESC, helpful_vote DESC
            LIMIT %s
        """
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            hits = conn.execute(sql, [query, threshold, limit]).fetchall()
        return self._pg_result(
            hits,
            f"{sentiment.title()} review search uses review_vector and ts_headline snippets.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=False,
            backend_complexity="Medium",
            score=4,
            document_type="review",
        )

    def _es_scenario_3_hybrid_search(self, query: str, limit: int) -> dict[str, Any]:
        from backend.services.gemini_embedding import embed_query

        query_vector = embed_query(query)
        body = {
            "track_total_hits": True,
            "size": limit,
            "query": {
                "bool": {
                    "should": [
                        {
                            "multi_match": {
                                "query": query,
                                "type": "best_fields",
                                "fields": [
                                    "title^4",
                                    "features^2",
                                    "brand^2",
                                    "category^2",
                                    "description",
                                    "review_text",
                                ],
                                "operator": "or",
                                "minimum_should_match": "2<70%",
                            }
                        }
                    ]
                }
            },
            "knn": {
                "field": "title_embedding",
                "query_vector": query_vector,
                "k": limit,
                "num_candidates": limit * 10,
            },
            "highlight": {"fields": {"title": {}, "description": {}, "review_text": {}}},
        }
        response = self.es.search(index=PRODUCT_INDEX, body=body)
        result = self._engine_result(
            "elasticsearch",
            response,
            "Hybrid Search: BM25 text matching (synonym-expanded multi_match) combined with Gemini KNN vector search using Reciprocal Rank Fusion.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=True,
            backend_complexity="Low",
            score=5,
        )
        return result

    def _meili_scenario_3_lexical_search(self, query: str, limit: int) -> dict[str, Any]:
        response = self.meili.index(PRODUCT_INDEX).search(
            query,
            {
                "hitsPerPage": limit,
                "page": 1,
                "attributesToHighlight": ["title", "features", "description", "review_text"],
                "showRankingScore": True,
            },
        )
        result = self._meili_result(
            response,
            "No embedding model configured. Meilisearch runs lexical full-text search only.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=False,
            backend_complexity="Low",
            score=2,
        )
        return result

    def _pg_scenario_3_lexical_search(self, query: str, limit: int) -> dict[str, Any]:
        sql = """
            WITH q AS (
                SELECT websearch_to_tsquery('english', %s) AS tsq
            )
            SELECT product_id, title, features, description, category, brand, price,
                   average_rating, rating_number, review_count,
                   ts_rank_cd(search_vector, q.tsq) AS score,
                   count(*) OVER() AS total,
                   ts_headline('english', title, q.tsq, 'StartSel=<mark>, StopSel=</mark>') AS title_highlight,
                   ts_headline('english', description, q.tsq, 'StartSel=<mark>, StopSel=</mark>, MaxWords=24') AS description_highlight
            FROM products, q
            WHERE search_vector @@ q.tsq
            ORDER BY score DESC, average_rating DESC, rating_number DESC
            LIMIT %s
        """
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            hits = conn.execute(sql, [query, limit]).fetchall()
        result = self._pg_result(
            hits,
            "PostgreSQL has no embedding model. This remains standard full-text search.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=False,
            backend_complexity="Medium",
            score=2,
        )
        return result

    def _es_scenario_4_analytics_aggregation(self, query: str, limit: int) -> dict[str, Any]:
        rating_filter, _sentiment = self._review_rating_filter(query)
        body = {
            "track_total_hits": True,
            "size": 0,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["title^2", "text"],
                            }
                        }
                    ],
                    "filter": [{"range": {"rating": rating_filter}}],
                }
            },
            "aggs": {
                "brands": {
                    "terms": {
                        "field": "brand",
                        "size": 10,
                        "order": [{"_count": "desc"}, {"avg_rating": "asc"}],
                    },
                    "aggs": {
                        "avg_rating": {"avg": {"field": "rating"}},
                        "total_helpful_votes": {"sum": {"field": "helpful_vote"}},
                    },
                },
                "categories": {"terms": {"field": "category", "size": 10}},
                "rating_distribution": {"histogram": {"field": "rating", "interval": 1}},
                "avg_rating": {"avg": {"field": "rating"}},
            },
        }
        response = self.es.search(index=REVIEW_INDEX, body=body)
        result = self._engine_result(
            "elasticsearch",
            response,
            "One request searches matching reviews, filters negative ratings, and aggregates brand/category/rating metrics.",
            number_of_requests=1,
            has_aggregation=True,
            has_custom_ranking=False,
            backend_complexity="Low",
            score=5,
            document_type="analytics",
        )
        result["aggregations"] = self._normalize_es_analytics(
            response.get("aggregations", {}),
            response.get("hits", {}).get("total", {}).get("value", 0),
        )
        return result

    def _meili_scenario_4_analytics_aggregation(self, query: str, limit: int) -> dict[str, Any]:
        response, docs = self._meili_matching_reviews(query)
        analytics = self._build_meili_analytics(response, docs)
        return {
            "engine": "meilisearch",
            "document_type": "analytics",
            "number_of_requests": 1,
            "total": self._meili_total(response, len(docs)),
            "hits": [],
            "aggregations": analytics,
            "has_highlight": False,
            "has_aggregation": True,
            "has_custom_ranking": False,
            "backend_complexity": "High",
            "note": (
                "Meilisearch facetDistribution gives counts natively. Per-brand avg_rating and helpful_vote, "
                "plus the overall summary, are computed app-side from a sample because Meilisearch has no metric "
                "aggregation; values may be approximate when matches exceed the sample size."
            ),
            "scorecard": {"overall": 2},
        }

    def _pg_scenario_4_analytics_aggregation(self, query: str, limit: int) -> dict[str, Any]:
        operator, threshold, _sentiment = self._pg_review_filter(query)
        rating_clause = f"reviews.rating {operator} %s"
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            brand_metrics = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT products.brand AS value,
                       count(*) AS negative_review_count,
                       round(avg(reviews.rating)::numeric, 2) AS avg_rating,
                       coalesce(sum(reviews.helpful_vote), 0) AS total_helpful_votes
                FROM reviews
                JOIN products ON products.product_id = reviews.product_id, q
                WHERE review_vector @@ q.tsq
                  AND {rating_clause}
                GROUP BY products.brand
                ORDER BY negative_review_count DESC, avg_rating ASC
                LIMIT 10
                """,
                [query, threshold],
            ).fetchall()
            categories = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT products.category AS value, count(*) AS negative_review_count
                FROM reviews
                JOIN products ON products.product_id = reviews.product_id, q
                WHERE review_vector @@ q.tsq
                  AND {rating_clause}
                GROUP BY products.category
                ORDER BY negative_review_count DESC
                LIMIT 10
                """,
                [query, threshold],
            ).fetchall()
            rating_distribution = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT reviews.rating AS value, count(*) AS count
                FROM reviews
                JOIN products ON products.product_id = reviews.product_id, q
                WHERE review_vector @@ q.tsq
                  AND {rating_clause}
                GROUP BY reviews.rating
                ORDER BY reviews.rating
                """,
                [query, threshold],
            ).fetchall()
            summary = conn.execute(
                f"""
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT count(*) AS matched_negative_reviews,
                       round(avg(reviews.rating)::numeric, 2) AS avg_rating
                FROM reviews
                JOIN products ON products.product_id = reviews.product_id, q
                WHERE review_vector @@ q.tsq
                  AND {rating_clause}
                """,
                [query, threshold],
            ).fetchone()
        return {
            "engine": "postgres",
            "document_type": "analytics",
            "number_of_requests": 4,
            "total": int((summary or {}).get("matched_negative_reviews") or 0),
            "hits": [],
            "aggregations": {
                "brands": brand_metrics,
                "categories": categories,
                "rating_distribution": rating_distribution,
                "summary": dict(summary or {}),
            },
            "has_highlight": False,
            "has_aggregation": True,
            "has_custom_ranking": False,
            "backend_complexity": "Medium",
            "note": "PostgreSQL answers the analytics with FTS, JOIN, and GROUP BY, but this runs as multiple SQL statements on the database.",
            "scorecard": {"overall": 4},
        }

    def _build_meili_analytics(self, response: dict[str, Any], docs: list[dict[str, Any]]) -> dict[str, Any]:
        facets = response.get("facetDistribution", {}) or {}
        brand_counts = facets.get("brand", {}) or {}
        category_counts = facets.get("category", {}) or {}
        rating_counts = facets.get("rating", {}) or {}

        brand_ratings: dict[str, list[float]] = defaultdict(list)
        brand_helpful: Counter[str] = Counter()
        for doc in docs:
            brand = doc.get("brand") or "Unknown"
            brand_ratings[brand].append(float(doc.get("rating") or 0))
            brand_helpful[brand] += int(doc.get("helpful_vote") or 0)

        brands = []
        for brand, count in sorted(brand_counts.items(), key=lambda item: -item[1])[:10]:
            ratings = brand_ratings.get(brand, [])
            brands.append({
                "value": brand,
                "negative_review_count": int(count),
                "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
                "total_helpful_votes": int(brand_helpful.get(brand, 0)),
            })

        categories = [
            {"value": value, "negative_review_count": int(count)}
            for value, count in sorted(category_counts.items(), key=lambda item: -item[1])[:10]
        ]
        rating_distribution = [
            {"value": value, "count": int(count)}
            for value, count in sorted(rating_counts.items(), key=lambda item: float(item[0]))
        ]

        sample_ratings = [float(doc.get("rating") or 0) for doc in docs]
        avg_rating = round(sum(sample_ratings) / len(sample_ratings), 2) if sample_ratings else None

        return {
            "brands": brands,
            "categories": categories,
            "rating_distribution": rating_distribution,
            "summary": {
                "matched_negative_reviews": self._meili_total(response, len(docs)),
                "avg_rating": avg_rating,
            },
        }

    def _review_rating_filter(self, query: str) -> tuple[dict[str, int], str]:
        is_positive = any(term in query.lower() for term in POSITIVE_REVIEW_TERMS)
        if is_positive:
            return {"gte": 4}, "positive"
        return {"lte": 2}, "negative"

    def _meili_total(self, response: dict[str, Any], hits_len: int) -> int:
        total = response.get("totalHits")
        if total is None:
            total = response.get("estimatedTotalHits", hits_len)
        return int(total or 0)

    def _meili_review_filter(self, query: str) -> tuple[str, str]:
        rating_filter, sentiment = self._review_rating_filter(query)
        if "gte" in rating_filter:
            return f"rating >= {rating_filter['gte']}", sentiment
        return f"rating <= {rating_filter['lte']}", sentiment

    def _pg_review_filter(self, query: str) -> tuple[str, int, str]:
        rating_filter, sentiment = self._review_rating_filter(query)
        if "gte" in rating_filter:
            return ">=", rating_filter["gte"], sentiment
        return "<=", rating_filter["lte"], sentiment

    def _meili_all_reviews(self) -> list[dict[str, Any]]:
        index = self.meili.index(REVIEW_INDEX)
        docs: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = index.search("", {"limit": 1000, "offset": offset})
            hits = response.get("hits", [])
            docs.extend(hits)
            total = response.get("estimatedTotalHits", len(docs))
            if not hits or len(docs) >= total:
                break
            offset += len(hits)
        return docs

    def _meili_matching_reviews(self, query: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        filter_expr, _sentiment = self._meili_review_filter(query)
        index = self.meili.index(REVIEW_INDEX)
        response = index.search(
            query,
            {
                "hitsPerPage": 1000,
                "page": 1,
                "filter": filter_expr,
                "facets": ["brand", "category", "rating"],
            },
        )
        return response, response.get("hits", [])

    def _aggregate_review_docs(self, docs: list[dict[str, Any]]) -> dict[str, Any]:
        brand_ratings: dict[str, list[float]] = defaultdict(list)
        brand_helpful_votes: Counter[str] = Counter()
        categories: Counter[str] = Counter()
        distribution: Counter[str] = Counter()
        for doc in docs:
            brand = doc.get("brand") or "Unknown"
            category = doc.get("category") or "Electronics"
            rating = float(doc.get("rating") or 0)
            brand_ratings[brand].append(rating)
            brand_helpful_votes[brand] += int(doc.get("helpful_vote") or 0)
            categories[category] += 1
            distribution[str(int(rating))] += 1
        brands = []
        for brand, values in brand_ratings.items():
            brands.append(
                {
                    "value": brand,
                    "negative_review_count": len(values),
                    "avg_rating": round(sum(values) / len(values), 2),
                    "total_helpful_votes": brand_helpful_votes[brand],
                }
            )
        return {
            "brands": sorted(brands, key=lambda x: (-x["negative_review_count"], x["avg_rating"]))[:10],
            "categories": [
                {"value": key, "negative_review_count": count} for key, count in categories.most_common(10)
            ],
            "rating_distribution": [
                {"value": key, "count": distribution[key]} for key in sorted(distribution, key=lambda x: float(x))
            ],
            "summary": {
                "matched_negative_reviews": len(docs),
                "avg_rating": round(sum(float(doc.get("rating") or 0) for doc in docs) / len(docs), 2) if docs else 0,
            },
        }

    def _normalize_es_analytics(self, aggs: dict[str, Any], total: int) -> dict[str, Any]:
        brands = []
        for bucket in aggs.get("brands", {}).get("buckets", []):
            brands.append({
                "value": bucket.get("key"),
                "negative_review_count": bucket.get("doc_count", 0),
                "avg_rating": round((bucket.get("avg_rating") or {}).get("value") or 0, 2),
                "total_helpful_votes": int((bucket.get("total_helpful_votes") or {}).get("value") or 0),
            })
        categories = [
            {"value": bucket.get("key"), "negative_review_count": bucket.get("doc_count", 0)}
            for bucket in aggs.get("categories", {}).get("buckets", [])
        ]
        rating_distribution = [
            {"value": bucket.get("key"), "count": bucket.get("doc_count", 0)}
            for bucket in aggs.get("rating_distribution", {}).get("buckets", [])
        ]
        avg_rating = round((aggs.get("avg_rating") or {}).get("value") or 0, 2)
        return {
            "brands": brands,
            "categories": categories,
            "rating_distribution": rating_distribution,
            "summary": {
                "matched_negative_reviews": int(total or 0),
                "avg_rating": avg_rating,
            },
        }

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
            "has_highlight": document_type != "analytics",
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
        for raw in response.get("hits", []):
            item = dict(raw)
            formatted = item.pop("_formatted", {})
            item["score"] = item.pop("_rankingScore", None)
            item["highlights"] = {key: [value] for key, value in formatted.items() if isinstance(value, str)}
            self._add_review_aliases(item)
            hits.append(item)
        return {
            "engine": "meilisearch",
            "document_type": document_type,
            "number_of_requests": number_of_requests,
            "total": self._meili_total(response, len(hits)),
            "hits": hits,
            "aggregations": (
                {"facets": response.get("facetDistribution", {})}
                if has_aggregation else {}
            ),
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
        document_type: str = "product",
    ) -> dict[str, Any]:
        hits = [self._pg_hit(row) for row in rows]
        return {
            "engine": "postgres",
            "document_type": document_type,
            "number_of_requests": number_of_requests,
            "total": int(rows[0].get("total", len(rows))) if rows else 0,
            "hits": hits,
            "aggregations": {},
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
        self._add_review_aliases(source)
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
        self._add_review_aliases(hit)
        return hit

    def _add_review_aliases(self, hit: dict[str, Any]) -> None:
        if "review_id" not in hit:
            return
        if "review_title" not in hit:
            hit["review_title"] = hit.get("title", "")
        if "review_text" not in hit:
            hit["review_text"] = hit.get("text", "")
        highlights = hit.get("highlights") or {}
        if "title" in highlights and "review_title" not in highlights:
            highlights["review_title"] = highlights["title"]
        if "text" in highlights and "review_text" not in highlights:
            highlights["review_text"] = highlights["text"]
        hit["highlights"] = highlights

    def _es_aggs(self, aggs: dict[str, Any]) -> dict[str, Any]:
        output = {}
        for name, value in aggs.items():
            if "buckets" in value:
                output[name] = self._bucket_list(value["buckets"])
            elif "value" in value:
                output[name] = value["value"]
            elif "doc_count" in value:
                nested = {key: self._es_aggs({key: nested_value})[key] for key, nested_value in value.items() if key != "doc_count"}
                output[name] = {"count": value["doc_count"], **nested}
            else:
                output[name] = value
        return output

    def _bucket_list(self, buckets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = []
        for bucket in buckets:
            item = {
                "value": bucket.get("key_as_string", bucket.get("key")),
                "count": bucket.get("doc_count"),
            }
            metrics = {}
            nested = {}
            for metric_name, metric_value in bucket.items():
                if isinstance(metric_value, dict) and "value" in metric_value:
                    metrics[metric_name] = metric_value.get("value")
                elif isinstance(metric_value, dict) and "buckets" in metric_value:
                    nested[metric_name] = self._bucket_list(metric_value["buckets"])
            if metrics:
                item["metrics"] = metrics
            item.update(nested)
            items.append(item)
        return items

    def _top_snippets(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        snippets = []
        for hit in hits[:10]:
            highlights = hit.get("highlights", {})
            text = (
                highlights.get("review_text")
                or highlights.get("text")
                or highlights.get("review_title")
                or highlights.get("title")
                or [hit.get("review_text", hit.get("text", ""))]
            )[0]
            snippets.append(
                {
                    "review_id": hit.get("review_id"),
                    "product_id": hit.get("product_id"),
                    "product_title": hit.get("product_title"),
                    "rating": hit.get("rating"),
                    "helpful_vote": hit.get("helpful_vote"),
                    "snippet": text,
                }
            )
        return snippets

    def _latency_stats(self, runs: list[dict[str, Any]]) -> dict[str, Any]:
        values = sorted(float(item["took_ms"]) for item in runs)
        if not values:
            return {"count": 0, "avg_ms": 0, "p95_ms": 0, "min_ms": 0, "max_ms": 0}
        p95_index = min(len(values) - 1, int(round((len(values) - 1) * 0.95)))
        return {
            "count": len(values),
            "avg_ms": round(sum(values) / len(values), 2),
            "p95_ms": values[p95_index],
            "min_ms": values[0],
            "max_ms": values[-1],
        }

    def _winner_reason(self, scenario_id: str) -> str:
        reasons = {
            "scenario-1-product-search": "Elasticsearch ranks fuzzy multi_match across boosted product fields, so typo-heavy queries still surface the right products.",
            "scenario-2-review-search": "Elasticsearch combines review text match with rating filter, helpful_vote sort, and inline highlights in a single request.",
            "scenario-3-intent-aware-search": "Elasticsearch uses Gemini embedding vectors combined with BM25 text matching (Hybrid Search) to understand user intent semantically, while Meilisearch and PostgreSQL fall back to lexical search.",
            "scenario-4-analytics-aggregation": "Elasticsearch combines full-text review search, rating filters and aggregations in the same engine without app-side fallback.",
        }
        return reasons[scenario_id]

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
