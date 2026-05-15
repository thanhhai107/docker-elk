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

QUERY_OPTIONS = PRODUCT_DISCOVERY_QUERIES + REVIEW_DEEP_SEARCH_QUERIES + RECOMMENDATION_QUERIES

SCENARIOS: dict[str, dict[str, Any]] = {
    "act-1-product-discovery": {
        "title": "ACT 1: Keyword Product Search",
        "flow_name": "Keyword Product Search",
        "default_query": PRODUCT_DISCOVERY_QUERIES[0],
        "user_action": "Search products with imperfect keywords.",
        "demo_goal": "Find the right product even when the query has typos, missing terms, or near-synonyms.",
        "difference": "Shows fuzzy search, field boosting, and ranking over title, features, and description.",
        "summary": (
            "Keyword product search for imperfect user queries. Elasticsearch demonstrates fuzzy "
            "search, boosted product fields, and flexible ranking across title, features, and description."
        ),
    },
    "act-2-review-deep-search": {
        "title": "ACT 2: Review Deep Search",
        "flow_name": "Review Deep Search",
        "default_query": REVIEW_DEEP_SEARCH_QUERIES[0],
        "user_action": "Search deeply inside review content.",
        "demo_goal": "Find specific reviews that mention a user problem, quality signal, or lived experience.",
        "difference": (
            "Searches review_title / review_text with highlight, sentiment routing, "
            "rating filters, and helpful_vote sorting."
        ),
        "summary": (
            "Deep review search over logical review_title/review_text fields with snippets, rating "
            "filters, sentiment routing, and helpful_vote sorting."
        ),
    },
    "act-3-review-analytics": {
        "title": "ACT 3: Review Analytics & Aggregation",
        "flow_name": "Review Analytics & Aggregation",
        "default_query": "overheating",
        "user_action": "Search a topic and summarize the insight.",
        "demo_goal": "Answer which brands/categories are associated with an issue and how ratings are distributed.",
        "difference": (
            "Elasticsearch combines search plus aggregation/facets in one engine; "
            "Meilisearch/PostgreSQL need app-side or SQL work."
        ),
        "summary": (
            "Review analytics workflow for turning a topic into brand/category/rating insights. "
            "Elasticsearch combines search, aggregation, and facets inside one engine."
        ),
    },
    "act-4-hybrid-recommendation": {
        "title": "ACT 4: Semantic Recommendation",
        "flow_name": "Semantic Recommendation",
        "default_query": RECOMMENDATION_QUERIES[0],
        "user_action": "Enter a natural-language need that is longer than a keyword query.",
        "demo_goal": "Recommend products using intent, product fields, review evidence, and rating signals.",
        "difference": "Shows the difference between traditional keyword search and smarter search/recommendation.",
        "summary": (
            "Semantic recommendation workflow for natural-language needs. Elasticsearch blends "
            "intent expansion, product fields, review evidence, and rating/review signals."
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
            "flow_name": scenario["flow_name"],
            "user_action": scenario["user_action"],
            "demo_goal": scenario["demo_goal"],
            "difference": scenario["difference"],
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
