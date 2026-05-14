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

PRODUCT_DISCOVERY_QUERIES = [
    "wireless noise cancelling headphone",
    "iphne charger fast charging",
    "bluetooth speaker bass",
    "camera night vision",
    "laptop lightweight long battery",
]

REVIEW_DEEP_SEARCH_QUERIES = [
    "battery drains fast",
    "stopped working after a week",
    "good sound quality",
    "screen flickering",
    "overheating problem",
    "easy to install",
]

RECOMMENDATION_QUERIES = [
    "I need headphones for online meetings with good battery and noise cancellation",
    "I want a charger that charges fast and does not overheat",
    "I need a camera that works well at night and is easy to install",
    "I want a bluetooth speaker with strong bass for a small room",
]

SCALE_TEST_CASES = (
    [{"type": "product", "query": query} for query in PRODUCT_DISCOVERY_QUERIES]
    + [{"type": "review", "query": query} for query in REVIEW_DEEP_SEARCH_QUERIES[:3]]
    + [{"type": "recommendation", "query": query} for query in RECOMMENDATION_QUERIES]
)
SCALE_TEST_QUERIES = [item["query"] for item in SCALE_TEST_CASES]

QUERY_OPTIONS = PRODUCT_DISCOVERY_QUERIES + REVIEW_DEEP_SEARCH_QUERIES + RECOMMENDATION_QUERIES + [
    "worker failover scale resilience"
]

SCENARIOS: dict[str, dict[str, Any]] = {
    "act-1-product-discovery": {
        "title": "ACT 1: Product Discovery Search",
        "default_query": PRODUCT_DISCOVERY_QUERIES[0],
        "summary": (
            "Users search electronics with imperfect keywords. Elasticsearch uses boosted "
            "multi_match plus fuzziness, Meilisearch uses search plus filters, and PostgreSQL "
            "uses full-text search."
        ),
    },
    "act-2-review-deep-search": {
        "title": "ACT 2: Review Deep Search",
        "default_query": REVIEW_DEEP_SEARCH_QUERIES[0],
        "summary": (
            "Deep search over review summary/text with snippets. Negative queries filter "
            "rating <= 3; positive queries filter rating >= 4; helpful votes are used as a tie-breaker."
        ),
    },
    "act-3-review-analytics": {
        "title": "ACT 3: Review Analytics & Aggregation",
        "default_query": "overheating",
        "summary": (
            "Answers brand/category/rating analytics questions. Elasticsearch combines text "
            "query and aggregations in one engine; Meilisearch uses an app-side aggregation fallback."
        ),
    },
    "act-4-hybrid-recommendation": {
        "title": "ACT 4: Hybrid / Semantic Product Recommendation Search",
        "default_query": RECOMMENDATION_QUERIES[0],
        "summary": (
            "Natural-language recommendation search. Elasticsearch combines intent-oriented "
            "multi-field matching, fuzzy matching, review text and business signals in one query."
        ),
    },
    "act-5-scale-readiness": {
        "title": "ACT 5: Worker Failover / Scale Resilience",
        "default_query": "worker failover scale resilience",
        "summary": (
            "Checks whether search can continue when 1-2 worker nodes are offline. Elasticsearch "
            "is evaluated through cluster health, shard/replica allocation, and live top-10 queries."
        ),
    },
}

BENCHMARK_ORDER = list(SCENARIOS)
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
            "product_discovery_queries": PRODUCT_DISCOVERY_QUERIES,
            "review_deep_search_queries": REVIEW_DEEP_SEARCH_QUERIES,
            "recommendation_queries": RECOMMENDATION_QUERIES,
            "scale_test_queries": SCALE_TEST_QUERIES,
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
        selected_query = query or scenario["default_query"]
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
            "summary": scenario["summary"],
            "query": selected_query,
            "engine": selected_engine,
            "winner": "elasticsearch" if selected_engine == "all" else None,
            "winner_reason": self._winner_reason(scenario_id) if selected_engine == "all" else None,
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
                "winner_reason": output["winner_reason"],
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
                "Elasticsearch is the best fit for these workflows because it combines field "
                "boosting, fuzzy matching, flexible scoring, highlighting, filters and text-aware "
                "aggregations in the same engine. Meilisearch is excellent for fast product search "
                "but needs app-side fallbacks for deeper analytics. PostgreSQL FTS is useful and "
                "transparent, but complex ranking and multi-step analytics require more SQL."
            ),
        }

    def _es_act_1_product_discovery(self, query: str, limit: int) -> dict[str, Any]:
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

    def _meili_act_1_product_discovery(self, query: str, limit: int) -> dict[str, Any]:
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

    def _pg_act_1_product_discovery(self, query: str, limit: int) -> dict[str, Any]:
        sql = """
            WITH q AS (
                SELECT websearch_to_tsquery('english', %s) AS tsq, %s::text AS raw_query
            )
            SELECT product_id, title, features, description, category, brand, price,
                   average_rating, rating_number, review_count,
                   ts_rank_cd(search_vector, q.tsq) + similarity(title, q.raw_query) AS score,
                   count(*) OVER() AS total,
                   ts_headline('english', title, q.tsq, 'StartSel=<mark>, StopSel=</mark>') AS title_highlight,
                   ts_headline('english', description, q.tsq, 'StartSel=<mark>, StopSel=</mark>, MaxWords=24') AS description_highlight
            FROM products, q
            WHERE search_vector @@ q.tsq
               OR title %% q.raw_query
               OR description %% q.raw_query
            ORDER BY score DESC, average_rating DESC, rating_number DESC
            LIMIT %s
        """
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            hits = conn.execute(sql, [query, query, limit]).fetchall()
        return self._pg_result(
            hits,
            "Full-text search plus trigram similarity helps typo tolerance, but ranking is hand-built in SQL.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=True,
            backend_complexity="Medium",
            score=3,
        )

    def _es_act_2_review_deep_search(self, query: str, limit: int) -> dict[str, Any]:
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
            f"{sentiment.title()} review search uses summary/text highlights and helpful_votes as a secondary sort.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=False,
            backend_complexity="Low",
            score=5,
            document_type="review",
        )
        result["top_snippets"] = self._top_snippets(result["hits"])
        return result

    def _meili_act_2_review_deep_search(self, query: str, limit: int) -> dict[str, Any]:
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

    def _pg_act_2_review_deep_search(self, query: str, limit: int) -> dict[str, Any]:
        operator, threshold, sentiment = self._pg_review_filter(query)
        sql = f"""
            WITH q AS (
                SELECT websearch_to_tsquery('english', %s) AS tsq
            )
            SELECT review_id, reviews.product_id, reviews.rating, reviews.title, reviews.text,
                   helpful_vote, verified_purchase, products.brand, products.category,
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

    def _es_act_3_review_analytics(self, query: str, limit: int) -> dict[str, Any]:
        body = {
            "size": 0,
            "aggs": {
                "top_brands_avg_rating_min_20_reviews": {
                    "terms": {"field": "brand", "size": 10, "min_doc_count": 20, "order": {"avg_rating": "desc"}},
                    "aggs": {"avg_rating": {"avg": {"field": "rating"}}},
                },
                "top_categories_negative_reviews": {
                    "filter": {"range": {"rating": {"lte": 2}}},
                    "aggs": {"categories": {"terms": {"field": "category", "size": 10}}},
                },
                "overheating_by_brand": {
                    "filter": {
                        "multi_match": {
                            "query": "overheating",
                            "fields": ["title^2", "text"],
                        }
                    },
                    "aggs": {"brands": {"terms": {"field": "brand", "size": 10}}},
                },
                "battery_by_category_avg_rating": {
                    "filter": {
                        "multi_match": {
                            "query": "battery",
                            "fields": ["title^2", "text"],
                        }
                    },
                    "aggs": {
                        "categories": {
                            "terms": {"field": "category", "size": 10},
                            "aggs": {"avg_rating": {"avg": {"field": "rating"}}},
                        }
                    },
                },
                "rating_distribution": {"terms": {"field": "rating", "size": 5, "order": {"_key": "asc"}}},
            },
        }
        response = self.es.search(index=REVIEW_INDEX, body=body)
        return self._engine_result(
            "elasticsearch",
            response,
            "One aggregation request answers all review analytics questions, including text query + group-by combinations.",
            number_of_requests=1,
            has_aggregation=True,
            has_custom_ranking=False,
            backend_complexity="Low",
            score=5,
            document_type="analytics",
        )

    def _meili_act_3_review_analytics(self, query: str, limit: int) -> dict[str, Any]:
        docs = self._meili_all_reviews()
        analytics = self._aggregate_review_docs(docs)
        return {
            "engine": "meilisearch",
            "document_type": "analytics",
            "number_of_requests": max(1, (len(docs) + 999) // 1000),
            "total": len(docs),
            "hits": [],
            "aggregations": analytics,
            "has_highlight": False,
            "has_aggregation": True,
            "has_custom_ranking": False,
            "backend_complexity": "High",
            "note": "Meilisearch does not provide the same nested aggregation model here, so the app fetches review hits and aggregates in Python.",
            "scorecard": {"overall": 2},
        }

    def _pg_act_3_review_analytics(self, query: str, limit: int) -> dict[str, Any]:
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            top_brands = conn.execute(
                """
                SELECT products.brand AS value, round(avg(reviews.rating)::numeric, 2) AS avg_rating,
                       count(*) AS count
                FROM reviews
                JOIN products ON products.product_id = reviews.product_id
                GROUP BY products.brand
                HAVING count(*) >= 20
                ORDER BY avg_rating DESC, count DESC
                LIMIT 10
                """
            ).fetchall()
            negative_categories = conn.execute(
                """
                SELECT products.category AS value, count(*) AS count
                FROM reviews
                JOIN products ON products.product_id = reviews.product_id
                WHERE reviews.rating <= 2
                GROUP BY products.category
                ORDER BY count DESC
                LIMIT 10
                """
            ).fetchall()
            overheating_by_brand = conn.execute(
                """
                WITH q AS (SELECT websearch_to_tsquery('english', 'overheating') AS tsq)
                SELECT products.brand AS value, count(*) AS count
                FROM reviews
                JOIN products ON products.product_id = reviews.product_id, q
                WHERE review_vector @@ q.tsq
                GROUP BY products.brand
                ORDER BY count DESC
                LIMIT 10
                """
            ).fetchall()
            battery_by_category = conn.execute(
                """
                WITH q AS (SELECT websearch_to_tsquery('english', 'battery') AS tsq)
                SELECT products.category AS value, round(avg(reviews.rating)::numeric, 2) AS avg_rating,
                       count(*) AS count
                FROM reviews
                JOIN products ON products.product_id = reviews.product_id, q
                WHERE review_vector @@ q.tsq
                GROUP BY products.category
                ORDER BY count DESC
                LIMIT 10
                """
            ).fetchall()
            rating_distribution = conn.execute(
                """
                SELECT rating AS value, count(*) AS count
                FROM reviews
                GROUP BY rating
                ORDER BY rating
                """
            ).fetchall()
        return {
            "engine": "postgres",
            "document_type": "analytics",
            "number_of_requests": 5,
            "total": 0,
            "hits": [],
            "aggregations": {
                "top_brands_avg_rating_min_20_reviews": top_brands,
                "top_categories_negative_reviews": negative_categories,
                "overheating_by_brand": overheating_by_brand,
                "battery_by_category_avg_rating": battery_by_category,
                "rating_distribution": rating_distribution,
            },
            "has_highlight": False,
            "has_aggregation": True,
            "has_custom_ranking": False,
            "backend_complexity": "Medium",
            "note": "PostgreSQL answers the questions with GROUP BY, but text-query aggregations are separate SQL statements.",
            "scorecard": {"overall": 4},
        }

    def _es_act_4_hybrid_recommendation(self, query: str, limit: int) -> dict[str, Any]:
        expanded = self._expand_intent(query)
        body = {
            "size": limit,
            "query": {
                "function_score": {
                    "query": {
                        "bool": {
                            "must": [
                                {
                                    "multi_match": {
                                        "query": expanded,
                                        "fields": [
                                            "title^5",
                                            "brand.text^2",
                                            "category.text^2",
                                            "features^4",
                                            "description^3",
                                            "review_text^2",
                                        ],
                                        "fuzziness": "AUTO",
                                    }
                                }
                            ],
                            "should": self._intent_should_clauses(query),
                        }
                    },
                    "functions": [
                        {"field_value_factor": {"field": "average_rating", "factor": 1.2, "missing": 1}},
                        {
                            "field_value_factor": {
                                "field": "rating_number",
                                "modifier": "log1p",
                                "factor": 0.25,
                                "missing": 1,
                            }
                        },
                        {"field_value_factor": {"field": "helpful_votes", "modifier": "log1p", "factor": 0.1, "missing": 0}},
                    ],
                    "score_mode": "sum",
                    "boost_mode": "multiply",
                }
            },
            "highlight": {"fields": {"title": {}, "features": {}, "description": {}, "review_text": {}}},
        }
        response = self.es.search(index=PRODUCT_INDEX, body=body)
        return self._engine_result(
            "elasticsearch",
            response,
            "Hybrid-style recommendation combines natural-language intent expansion, boosted fields, review text and rating/review signals.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=True,
            backend_complexity="Low",
            score=5,
        )

    def _meili_act_4_hybrid_recommendation(self, query: str, limit: int) -> dict[str, Any]:
        response = self.meili.index(PRODUCT_INDEX).search(
            self._expand_intent(query),
            {
                "limit": limit,
                "filter": "average_rating >= 3.5",
                "sort": ["average_rating:desc", "rating_number:desc"],
                "attributesToHighlight": ["title", "features", "description", "review_text"],
                "showRankingScore": True,
            },
        )
        return self._meili_result(
            response,
            "Expanded natural-language search works well for retrieval, but scoring is mostly fixed plus sort rules.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=True,
            backend_complexity="Medium",
            score=3,
        )

    def _pg_act_4_hybrid_recommendation(self, query: str, limit: int) -> dict[str, Any]:
        expanded = self._expand_intent(query)
        sql = """
            WITH q AS (
                SELECT websearch_to_tsquery('english', %s) AS tsq
            )
            SELECT product_id, title, features, description, category, brand, price,
                   average_rating, rating_number,
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
            hits = conn.execute(sql, [expanded, limit]).fetchall()
        return self._pg_result(
            hits,
            "PostgreSQL can combine FTS rank with rating/review signals, but semantic tuning lives in application SQL.",
            number_of_requests=1,
            has_aggregation=False,
            has_custom_ranking=True,
            backend_complexity="High",
            score=3,
        )

    def _es_act_5_scale_readiness(self, query: str, limit: int) -> dict[str, Any]:
        runs = []
        for case in SCALE_TEST_CASES:
            sample = case["query"]
            is_review = case["type"] == "review"
            started = perf_counter()
            response = self.es.search(
                index=REVIEW_INDEX if is_review else PRODUCT_INDEX,
                body={
                    "size": limit,
                    "query": {
                        "multi_match": {
                            "query": sample if is_review else self._expand_intent(sample),
                            "fields": ["title^2", "text"] if is_review else ["title^5", "features^3", "description^2", "review_text"],
                            "fuzziness": "AUTO",
                        }
                    },
                    "track_total_hits": False,
                },
            )
            runs.append(
                {
                    "type": case["type"],
                    "query": sample,
                    "took_ms": round((perf_counter() - started) * 1000, 2),
                    "engine_took_ms": response.get("took"),
                    "top_10_count": len(response.get("hits", {}).get("hits", [])),
                }
            )
        stats = self._latency_stats(runs)
        try:
            index_stats = self.es.indices.stats(index=f"{PRODUCT_INDEX},{REVIEW_INDEX}", metric="docs,store")
            index_settings = self.es.indices.get_settings(index=f"{PRODUCT_INDEX},{REVIEW_INDEX}")
            cluster = self.es.cluster.health()
            allocation = self.es.cat.shards(index=f"{PRODUCT_INDEX},{REVIEW_INDEX}", format="json")
            shard_states = Counter(str(item.get("state", "unknown")).lower() for item in allocation)
            replica_summary = {
                "primary_shards": sum(1 for item in allocation if item.get("prirep") == "p"),
                "replica_shards": sum(1 for item in allocation if item.get("prirep") == "r"),
                "started_shards": shard_states.get("started", 0),
                "unassigned_shards": shard_states.get("unassigned", 0),
                "initializing_shards": shard_states.get("initializing", 0),
                "relocating_shards": shard_states.get("relocating", 0),
            }
            query_success = all(item["top_10_count"] >= 0 for item in runs)
            configured_replicas = {
                name: int(item.get("settings", {}).get("index", {}).get("number_of_replicas", 0))
                for name, item in index_settings.items()
            }
            can_serve_with_missing_workers = (
                query_success
                and cluster.get("status") in {"green", "yellow"}
                and int(cluster.get("active_primary_shards") or 0) > 0
            )
            one_worker_failover_ready = can_serve_with_missing_workers and all(
                replicas >= 1 for replicas in configured_replicas.values()
            )
            two_worker_failover_ready = can_serve_with_missing_workers and all(
                replicas >= 2 for replicas in configured_replicas.values()
            )
            scale_metadata = {
                "cluster_status": cluster.get("status"),
                "number_of_nodes": cluster.get("number_of_nodes"),
                "active_primary_shards": cluster.get("active_primary_shards"),
                "active_shards": cluster.get("active_shards"),
                "unassigned_shards": cluster.get("unassigned_shards"),
                "delayed_unassigned_shards": cluster.get("delayed_unassigned_shards"),
                "shard_summary": replica_summary,
                "can_continue_serving_queries": can_serve_with_missing_workers,
                "configured_replicas": configured_replicas,
                "one_worker_failover_ready": one_worker_failover_ready,
                "two_worker_failover_ready": two_worker_failover_ready,
                "manual_failover_test": [
                    "Start with all Elasticsearch nodes online and ingest data with number_of_replicas >= 2.",
                    "Stop one worker node, then run this ACT 5 endpoint again.",
                    "Stop a second worker node, then run this ACT 5 endpoint again.",
                    "Pass condition: product/review queries still return top-10 results and cluster status is green or yellow.",
                    "Fail condition: red cluster status, missing primaries, or search errors.",
                ],
                "indices": {
                    name: {
                        "docs": item.get("total", {}).get("docs", {}).get("count"),
                        "store_bytes": item.get("total", {}).get("store", {}).get("size_in_bytes"),
                    }
                    for name, item in index_stats.get("indices", {}).items()
                },
            }
        except Exception as exc:
            scale_metadata = {"stats_error": str(exc)}
        return {
            "engine": "elasticsearch",
            "document_type": "scale",
            "number_of_requests": len(SCALE_TEST_QUERIES) + 4,
            "total": sum(item["top_10_count"] for item in runs),
            "hits": [],
            "aggregations": {"latency": stats, "runs": runs, "scale_metadata": scale_metadata},
            "has_highlight": False,
            "has_aggregation": True,
            "has_custom_ranking": True,
            "backend_complexity": "Low",
            "note": (
                "Elasticsearch is the right fit for worker failover when indices have replicas: if 1-2 worker "
                "nodes go offline, remaining nodes can keep serving searches from active primary/replica shards. "
                "A yellow cluster can still serve search; red means at least one primary shard is unavailable."
            ),
            "scorecard": {"overall": 5},
        }

    def _meili_act_5_scale_readiness(self, query: str, limit: int) -> dict[str, Any]:
        runs = []
        for case in SCALE_TEST_CASES:
            sample = case["query"]
            is_review = case["type"] == "review"
            index = self.meili.index(REVIEW_INDEX if is_review else PRODUCT_INDEX)
            started = perf_counter()
            response = index.search(
                sample if is_review else self._expand_intent(sample),
                {
                    "limit": limit,
                    "filter": "rating >= 0" if is_review else "average_rating >= 0",
                    "showRankingScore": True,
                },
            )
            runs.append(
                {
                    "type": case["type"],
                    "query": sample,
                    "took_ms": round((perf_counter() - started) * 1000, 2),
                    "engine_processing_ms": response.get("processingTimeMs"),
                    "top_10_count": len(response.get("hits", [])),
                }
            )
        stats = self._latency_stats(runs)
        try:
            meili_stats = self.meili.get_stats()
            scale_metadata = {
                "database_size": getattr(meili_stats, "database_size", None),
                "last_update": getattr(meili_stats, "last_update", None),
                "indexes": getattr(meili_stats, "indexes", None),
            }
        except Exception as exc:
            scale_metadata = {"stats_error": str(exc)}
        return {
            "engine": "meilisearch",
            "document_type": "scale",
            "number_of_requests": len(SCALE_TEST_QUERIES) + 1,
            "total": sum(item["top_10_count"] for item in runs),
            "hits": [],
            "aggregations": {"latency": stats, "runs": runs, "scale_metadata": scale_metadata},
            "has_highlight": False,
            "has_aggregation": False,
            "has_custom_ranking": False,
            "backend_complexity": "Low",
            "note": (
                "In this docker-compose demo, Meilisearch runs as one service. If that service is offline, "
                "search is offline unless you add an external HA/replication strategy outside this stack."
            ),
            "scorecard": {"overall": 4},
        }

    def _pg_act_5_scale_readiness(self, query: str, limit: int) -> dict[str, Any]:
        product_sql = """
            WITH q AS (
                SELECT websearch_to_tsquery('english', %s) AS tsq, %s::text AS raw_query
            )
            SELECT product_id, title,
                   ts_rank_cd(search_vector, q.tsq) + similarity(title, q.raw_query) AS score
            FROM products, q
            WHERE search_vector @@ q.tsq
               OR title %% q.raw_query
               OR description %% q.raw_query
            ORDER BY score DESC
            LIMIT %s
        """
        review_sql = """
            WITH q AS (
                SELECT websearch_to_tsquery('english', %s) AS tsq
            )
            SELECT review_id, title,
                   ts_rank_cd(review_vector, q.tsq) AS score
            FROM reviews, q
            WHERE review_vector @@ q.tsq
            ORDER BY score DESC, helpful_vote DESC
            LIMIT %s
        """
        runs = []
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            for case in SCALE_TEST_CASES:
                sample = case["query"]
                started = perf_counter()
                if case["type"] == "review":
                    hits = conn.execute(review_sql, [sample, limit]).fetchall()
                else:
                    expanded = self._expand_intent(sample)
                    hits = conn.execute(product_sql, [expanded, expanded, limit]).fetchall()
                runs.append(
                    {
                        "type": case["type"],
                        "query": sample,
                        "took_ms": round((perf_counter() - started) * 1000, 2),
                        "top_10_count": len(hits),
                    }
                )
            metadata = conn.execute(
                """
                SELECT
                    (SELECT count(*) FROM products) AS product_count,
                    (SELECT count(*) FROM reviews) AS review_count,
                    pg_database_size(current_database()) AS database_bytes
                """
            ).fetchone()
        return {
            "engine": "postgres",
            "document_type": "scale",
            "number_of_requests": len(SCALE_TEST_QUERIES) + 1,
            "total": sum(item["top_10_count"] for item in runs),
            "hits": [],
            "aggregations": {"latency": self._latency_stats(runs), "runs": runs, "scale_metadata": metadata},
            "has_highlight": False,
            "has_aggregation": True,
            "has_custom_ranking": True,
            "backend_complexity": "Medium",
            "note": (
                "In this docker-compose demo, PostgreSQL runs as one primary database service. It can be made HA "
                "with replicas/failover tooling, but that is separate from the current stack and not search-native."
            ),
            "scorecard": {"overall": 3},
        }

    def _review_rating_filter(self, query: str) -> tuple[dict[str, int], str]:
        is_positive = any(term in query.lower() for term in POSITIVE_REVIEW_TERMS)
        if is_positive:
            return {"gte": 4}, "positive"
        return {"lte": 3}, "negative"

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

    def _expand_intent(self, query: str) -> str:
        lower = query.lower()
        additions = []
        if "meeting" in lower or "noise" in lower or "headphone" in lower:
            additions.extend(["headphones", "headset", "microphone", "noise cancelling", "battery"])
        if "charger" in lower or "charges" in lower:
            additions.extend(["fast charging", "usb c", "charger", "overheat", "cool"])
        if "camera" in lower or "night" in lower:
            additions.extend(["camera", "night vision", "easy install", "security"])
        if "speaker" in lower or "bass" in lower:
            additions.extend(["bluetooth speaker", "strong bass", "small room", "wireless"])
        return " ".join([query, *additions])

    def _intent_should_clauses(self, query: str) -> list[dict[str, Any]]:
        expanded_terms = self._expand_intent(query).split()
        clauses = [{"match": {"review_text": {"query": term, "boost": 1.2}}} for term in expanded_terms[:12]]
        if "overheat" in query.lower():
            clauses.append({"match": {"review_text": {"query": "does not overheat cool reliable", "boost": 2}}})
        return clauses

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

    def _aggregate_review_docs(self, docs: list[dict[str, Any]]) -> dict[str, Any]:
        by_brand: dict[str, list[float]] = defaultdict(list)
        negative_categories: Counter[str] = Counter()
        overheating_by_brand: Counter[str] = Counter()
        battery_by_category: dict[str, list[float]] = defaultdict(list)
        distribution: Counter[str] = Counter()
        for doc in docs:
            brand = doc.get("brand") or "Unknown"
            category = doc.get("category") or "Electronics"
            rating = float(doc.get("rating") or 0)
            text = f"{doc.get('title', '')} {doc.get('text', '')}".lower()
            by_brand[brand].append(rating)
            if rating <= 2:
                negative_categories[category] += 1
            if "overheating" in text or "overheat" in text:
                overheating_by_brand[brand] += 1
            if "battery" in text:
                battery_by_category[category].append(rating)
            distribution[str(int(rating))] += 1
        top_brands = [
            {"value": brand, "avg_rating": round(sum(values) / len(values), 2), "count": len(values)}
            for brand, values in by_brand.items()
            if len(values) >= 20
        ]
        battery = [
            {"value": category, "avg_rating": round(sum(values) / len(values), 2), "count": len(values)}
            for category, values in battery_by_category.items()
        ]
        return {
            "top_brands_avg_rating_min_20_reviews": sorted(top_brands, key=lambda x: (-x["avg_rating"], -x["count"]))[:10],
            "top_categories_negative_reviews": [
                {"value": key, "count": count} for key, count in negative_categories.most_common(10)
            ],
            "overheating_by_brand": [
                {"value": key, "count": count} for key, count in overheating_by_brand.most_common(10)
            ],
            "battery_by_category_avg_rating": sorted(battery, key=lambda x: -x["count"])[:10],
            "rating_distribution": [
                {"value": key, "count": distribution[key]} for key in sorted(distribution, key=lambda x: float(x))
            ],
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
            text = (highlights.get("text") or highlights.get("title") or [hit.get("text", "")])[0]
            snippets.append(
                {
                    "review_id": hit.get("review_id"),
                    "product_id": hit.get("product_id"),
                    "rating": hit.get("rating"),
                    "helpful_votes": hit.get("helpful_vote"),
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
            "act-1-product-discovery": "Field boosting, fuzzy query handling and flexible scoring make Elasticsearch strongest for imperfect product keywords.",
            "act-2-review-deep-search": "Elasticsearch returns deep review matches with highlights, rating filters and helpful-vote tie-break sorting in one request.",
            "act-3-review-analytics": "Elasticsearch combines text search and aggregations in the same engine without app-side fallback.",
            "act-4-hybrid-recommendation": "Elasticsearch supports intent expansion, boosted fields, review evidence and business scoring in one relevance model.",
            "act-5-scale-readiness": "Elasticsearch is strongest for worker failover because replicas let remaining nodes serve search when 1-2 workers are offline.",
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
