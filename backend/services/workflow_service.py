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

ADVANCED_KEYWORD_PRODUCT_QUERIES = [
    "wireles noise canclling headphnes sony",
    "wireless noise cancelling headphone",
    "iphne charger fast charging",
    "bluetooth speaker bass",
]

REVIEW_EVIDENCE_QUERIES = [
    "battery dies after a week",
    "battery problem",
    "stopped working after a week",
]

NATIVE_SEMANTIC_QUERIES = [
    "headphones for flights and office calls",
    "quiet headphones for working from home",
    "portable speaker for outdoor party with strong bass",
    "charger that fills phone battery quickly",
]

REVIEW_ANALYTICS_QUERIES = [
    "battery problem",
    "battery drain problem",
    "charging problem",
]

QUERY_OPTIONS = (
    ADVANCED_KEYWORD_PRODUCT_QUERIES
    + REVIEW_EVIDENCE_QUERIES
    + NATIVE_SEMANTIC_QUERIES
    + REVIEW_ANALYTICS_QUERIES
)

SCENARIOS: dict[str, dict[str, Any]] = {
    "scenario-1-full-text-keyword-search": {
        "title": "Scenario 1: Full-text/Keyword Search",
        "flow_name": "Full-text/Keyword Search",
        "default_query": ADVANCED_KEYWORD_PRODUCT_QUERIES[0],
        "secondary_query": REVIEW_EVIDENCE_QUERIES[0],
        "user_action": "Run typo-heavy product discovery, then retrieve review evidence for a concrete complaint.",
        "demo_goal": "Show keyword search under two practical conditions: typo-tolerant product discovery and highlighted review evidence.",
        "difference": (
            "Elasticsearch combines fuzzy multi-field product ranking with highlighted review evidence, "
            "rating filters, and helpful-vote sorting."
        ),
        "summary": (
            "Full-text/keyword workflow that combines product typo search and review evidence search. "
            "Elasticsearch demonstrates fuzzy boosted product search plus filtered/highlighted review snippets."
        ),
    },
    "scenario-2-semantic-search": {
        "title": "Scenario 2: Intent-Aware Search",
        "flow_name": "Intent-Aware Search",
        "default_query": NATIVE_SEMANTIC_QUERIES[0],
        "user_action": "Search by intent (paraphrased product use) without external embedding providers.",
        "demo_goal": "Show how each engine can still match intent-style queries when external models and user-provided embeddings are not allowed.",
        "difference": (
            "Elasticsearch uses synonym-aware multi_match (synonym_graph expands ANC/wireless/headphones/etc.). "
            "Meilisearch and PostgreSQL fall back to lexical full-text search."
        ),
        "summary": (
            "No-external-model intent workflow. Elasticsearch uses synonyms plus boosted multi_match over product metadata and review text; "
            "Meilisearch and PostgreSQL demonstrate what remains without curated synonyms: standard full-text retrieval."
        ),
    },
    "scenario-3-analytics-aggregation": {
        "title": "Scenario 3: Analytics & Aggregation",
        "flow_name": "Analytics & Aggregation",
        "default_query": REVIEW_ANALYTICS_QUERIES[0],
        "user_action": "Search review text for battery problem and summarize the matched negative reviews.",
        "demo_goal": "Answer which brands/categories receive the most battery-problem complaints and how ratings are distributed.",
        "difference": (
            "Elasticsearch combines search plus aggregation/facets in one engine; "
            "Meilisearch/PostgreSQL need app-side or SQL work."
        ),
        "summary": (
            "Review analytics workflow for turning a battery-problem topic into brand/category/rating insights. "
            "Elasticsearch combines full-text search, filters, aggregation, and facets inside one engine."
        ),
    },
}

SearchEngine = Literal["all", "elasticsearch", "meilisearch", "postgres"]

POSITIVE_REVIEW_TERMS = {"good", "great", "excellent", "easy", "quality", "works well", "install"}


class WorkflowService:
    def __init__(self) -> None:
        self.es = Elasticsearch(settings.elasticsearch_url, request_timeout=30)
        self.meili = meilisearch.Client(settings.meili_url, settings.meili_master_key)

    def list_scenarios(self) -> dict[str, Any]:
        return {
            "scenarios": SCENARIOS,
            "query_options": QUERY_OPTIONS,
            "keyword_product_queries": ADVANCED_KEYWORD_PRODUCT_QUERIES,
            "review_evidence_queries": REVIEW_EVIDENCE_QUERIES,
            "semantic_queries": NATIVE_SEMANTIC_QUERIES,
            "analytics_queries": REVIEW_ANALYTICS_QUERIES,
        }

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
        suffix = scenario_id.replace("-", "_")
        runners: list[tuple[str, Callable[[str, int], dict[str, Any]]]] = [
            ("elasticsearch", getattr(self, f"_es_{suffix}")),
            ("meilisearch", getattr(self, f"_meili_{suffix}")),
            ("postgres", getattr(self, f"_pg_{suffix}")),
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
            "user_action": scenario["user_action"],
            "demo_goal": scenario["demo_goal"],
            "difference": scenario["difference"],
            "summary": scenario["summary"],
            "query": selected_query,
            "queries": self._scenario_queries(scenario_id, selected_query),
            "engine": selected_engine,
            "winner": "elasticsearch" if selected_engine == "all" else None,
            "winner_reason": self._winner_reason(scenario_id) if selected_engine == "all" else None,
            "results": results,
        }

    def _scenario_queries(self, scenario_id: str, selected_query: str) -> dict[str, str]:
        if scenario_id == "scenario-1-full-text-keyword-search":
            return {
                "product_discovery": selected_query,
                "review_evidence": selected_query,
            }
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

    def _es_scenario_1_full_text_keyword_search(self, query: str, limit: int) -> dict[str, Any]:
        review_query = query
        product_result = self._es_product_keyword_search(query, limit)
        review_result = self._es_review_evidence_search(review_query, limit)
        return self._mixed_result(
            "elasticsearch",
            "Two Elasticsearch keyword requests: fuzzy boosted product discovery plus highlighted negative review evidence.",
            [
                self._result_section("Product discovery with typos", query, product_result),
                self._result_section("Review evidence snippets", review_query, review_result),
            ],
            number_of_requests=product_result["number_of_requests"] + review_result["number_of_requests"],
            has_custom_ranking=True,
            backend_complexity="Medium",
            score=5,
        )

    def _meili_scenario_1_full_text_keyword_search(self, query: str, limit: int) -> dict[str, Any]:
        review_query = query
        product_result = self._meili_product_keyword_search(query, limit)
        review_result = self._meili_review_evidence_search(review_query, limit)
        return self._mixed_result(
            "meilisearch",
            "Two Meilisearch keyword requests with typo tolerance, filters, and highlights, but less ranking control per field and signal.",
            [
                self._result_section("Product discovery with typos", query, product_result),
                self._result_section("Review evidence snippets", review_query, review_result),
            ],
            number_of_requests=product_result["number_of_requests"] + review_result["number_of_requests"],
            has_custom_ranking=False,
            backend_complexity="Low",
            score=3,
        )

    def _pg_scenario_1_full_text_keyword_search(self, query: str, limit: int) -> dict[str, Any]:
        review_query = query
        product_result = self._pg_product_keyword_search(query, limit)
        review_result = self._pg_review_evidence_search(review_query, limit)
        return self._mixed_result(
            "postgres",
            "Two PostgreSQL FTS queries. Review snippets work with ts_headline, but typo-heavy product discovery is weak without pg_trgm in this scenario.",
            [
                self._result_section("Product discovery with typos", query, product_result),
                self._result_section("Review evidence snippets", review_query, review_result),
            ],
            number_of_requests=product_result["number_of_requests"] + review_result["number_of_requests"],
            has_custom_ranking=False,
            backend_complexity="Medium",
            score=2,
        )

    def _es_product_keyword_search(self, query: str, limit: int) -> dict[str, Any]:
        body = {
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
                "limit": limit,
                "filter": "average_rating >= 0",
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
                "limit": limit,
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

    def _es_scenario_2_semantic_search(self, query: str, limit: int) -> dict[str, Any]:
        body = {
            "size": limit,
            "query": {
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
            },
            "highlight": {"fields": {"title": {}, "description": {}, "review_text": {}}},
        }
        response = self.es.search(index=PRODUCT_INDEX, body=body)
        result = self._engine_result(
            "elasticsearch",
            response,
            "Synonym-aware multi_match: the product_search analyzer expands intent terms (anc/headphones/wireless/etc.) at query time, then ranks across title, features, brand, category, description, and review_text.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=True,
            backend_complexity="Low",
            score=4,
        )
        result["semantic_capability"] = {
            "semantic_config": "synonym_graph filter (anc/headphones/wireless/cheap) on the product_search analyzer; multi_match across title, brand, category, features, description, review_text",
            "model_source": "none; intent is captured via curated synonyms instead of an embedding model",
            "conclusion": "Stays close to user intent without external embeddings, at the cost of true semantic generalization.",
        }
        return result

    def _meili_scenario_2_semantic_search(self, query: str, limit: int) -> dict[str, Any]:
        response = self.meili.index(PRODUCT_INDEX).search(
            query,
            {
                "limit": limit,
                "attributesToHighlight": ["title", "features", "description", "review_text"],
                "showRankingScore": True,
            },
        )
        result = self._meili_result(
            response,
            "No external model or user-provided embeddings are configured, so Meilisearch runs normal full-text search only.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=False,
            backend_complexity="Low",
            score=2,
        )
        result["semantic_capability"] = {
            "semantic_config": "none under this constraint",
            "model_source": "not configured; OpenAI/Hugging Face/Ollama/REST/user-provided embeddings are excluded",
            "conclusion": "Falls back to lexical full-text search.",
        }
        return result

    def _pg_scenario_2_semantic_search(self, query: str, limit: int) -> dict[str, Any]:
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
            "PostgreSQL core has no embedding model. Without external embeddings, this remains standard full-text search.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=False,
            backend_complexity="Medium",
            score=2,
        )
        result["semantic_capability"] = {
            "semantic_config": "none in PostgreSQL core",
            "model_source": "PostgreSQL core has no embedding generator; vector storage alone would still need embeddings",
            "conclusion": "Falls back to lexical full-text search.",
        }
        return result

    def _es_scenario_3_analytics_aggregation(self, query: str, limit: int) -> dict[str, Any]:
        body = {
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
                    "filter": [{"range": {"rating": {"lte": 2}}}],
                }
            },
            "aggs": {
                "brands": {
                    "terms": {"field": "brand", "size": 10},
                    "aggs": {
                        "avg_rating": {"avg": {"field": "rating"}},
                        "total_helpful_votes": {"sum": {"field": "helpful_vote"}},
                    },
                },
                "categories": {"terms": {"field": "category", "size": 10}},
                "rating_distribution": {"terms": {"field": "rating", "size": 5, "order": {"_key": "asc"}}},
                "avg_rating": {"avg": {"field": "rating"}},
            },
        }
        response = self.es.search(index=REVIEW_INDEX, body=body)
        return self._engine_result(
            "elasticsearch",
            response,
            "One request searches battery-problem reviews, filters negative ratings, and aggregates brand/category/rating metrics.",
            number_of_requests=1,
            has_aggregation=True,
            has_custom_ranking=False,
            backend_complexity="Low",
            score=5,
            document_type="analytics",
        )

    def _meili_scenario_3_analytics_aggregation(self, query: str, limit: int) -> dict[str, Any]:
        response, docs = self._meili_matching_reviews(query)
        analytics = self._aggregate_review_docs(docs)
        analytics["facets"] = response.get("facetDistribution", {})
        return {
            "engine": "meilisearch",
            "document_type": "analytics",
            "number_of_requests": 1,
            "total": response.get("estimatedTotalHits", len(docs)),
            "hits": [],
            "aggregations": analytics,
            "has_highlight": False,
            "has_aggregation": True,
            "has_custom_ranking": False,
            "backend_complexity": "High",
            "note": "Meilisearch returns facet counts for matched reviews, while avg rating and helpful-vote metrics are computed in the app.",
            "scorecard": {"overall": 2},
        }

    def _pg_scenario_3_analytics_aggregation(self, query: str, limit: int) -> dict[str, Any]:
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            brand_metrics = conn.execute(
                """
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT products.brand AS value,
                       count(*) AS negative_review_count,
                       round(avg(reviews.rating)::numeric, 2) AS avg_rating,
                       coalesce(sum(reviews.helpful_vote), 0) AS total_helpful_votes
                FROM reviews
                JOIN products ON products.product_id = reviews.product_id, q
                WHERE review_vector @@ q.tsq
                  AND reviews.rating <= 2
                GROUP BY products.brand
                ORDER BY negative_review_count DESC, avg_rating ASC
                LIMIT 10
                """,
                [query],
            ).fetchall()
            categories = conn.execute(
                """
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT products.category AS value, count(*) AS negative_review_count
                FROM reviews
                JOIN products ON products.product_id = reviews.product_id, q
                WHERE review_vector @@ q.tsq
                  AND reviews.rating <= 2
                GROUP BY products.category
                ORDER BY negative_review_count DESC
                LIMIT 10
                """,
                [query],
            ).fetchall()
            rating_distribution = conn.execute(
                """
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT reviews.rating AS value, count(*) AS count
                FROM reviews
                JOIN products ON products.product_id = reviews.product_id, q
                WHERE review_vector @@ q.tsq
                  AND reviews.rating <= 2
                GROUP BY reviews.rating
                ORDER BY reviews.rating
                """,
                [query],
            ).fetchall()
            summary = conn.execute(
                """
                WITH q AS (SELECT websearch_to_tsquery('english', %s) AS tsq)
                SELECT count(*) AS matched_negative_reviews,
                       round(avg(reviews.rating)::numeric, 2) AS avg_rating
                FROM reviews
                JOIN products ON products.product_id = reviews.product_id, q
                WHERE review_vector @@ q.tsq
                  AND reviews.rating <= 2
                """,
                [query],
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

    def _review_rating_filter(self, query: str) -> tuple[dict[str, int], str]:
        is_positive = any(term in query.lower() for term in POSITIVE_REVIEW_TERMS)
        if is_positive:
            return {"gte": 4}, "positive"
        return {"lte": 2}, "negative"

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
        index = self.meili.index(REVIEW_INDEX)
        response = index.search(
            query,
            {
                "limit": 1000,
                "filter": "rating <= 2",
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
            "scenario-1-full-text-keyword-search": "Elasticsearch combines fuzzy boosted product search with highlighted review evidence and flexible ranking signals.",
            "scenario-2-semantic-search": "Elasticsearch combines a curated synonym graph with boosted multi_match, so paraphrased intent queries still hit relevant products without an embedding model.",
            "scenario-3-analytics-aggregation": "Elasticsearch combines full-text review search, rating filters and aggregations in the same engine without app-side fallback.",
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
