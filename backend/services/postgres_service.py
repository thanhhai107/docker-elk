from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from backend.config import settings


class PostgresSearchService:
    engine = "postgres"

    def _filters(self, params: dict[str, Any]) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if params.get("brand"):
            clauses.append("brand = %s")
            values.append(params["brand"])
        if params.get("category"):
            clauses.append("category = %s")
            values.append(params["category"])
        if params.get("min_price") is not None:
            clauses.append("price >= %s")
            values.append(params["min_price"])
        if params.get("max_price") is not None:
            clauses.append("price <= %s")
            values.append(params["max_price"])
        if params.get("min_rating") is not None:
            clauses.append("rating >= %s")
            values.append(params["min_rating"])
        return clauses, values

    def search(self, params: dict[str, Any]) -> dict[str, Any]:
        query = params["q"].strip()
        limit = int(params.get("limit") or 10)
        filters, values = self._filters(params)
        where = " AND ".join(filters) if filters else "TRUE"

        sql = f"""
            WITH q AS (
                SELECT websearch_to_tsquery('english', %s) AS tsq, %s::text AS raw_query
            )
            SELECT
                product_id, title, description, category, brand, price, rating,
                review_count, avg_review_rating, loaded_review_count, helpful_votes,
                ts_rank(search_vector, q.tsq) + similarity(title, q.raw_query) AS score,
                ts_headline('english', title, q.tsq, 'StartSel=<mark>, StopSel=</mark>') AS title_highlight,
                ts_headline('english', description, q.tsq, 'StartSel=<mark>, StopSel=</mark>, MaxWords=24') AS description_highlight
            FROM products, q
            WHERE ({where})
              AND (
                search_vector @@ q.tsq
                OR title %% q.raw_query
                OR description %% q.raw_query
              )
            ORDER BY score DESC, rating DESC, review_count DESC
            LIMIT %s
        """

        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            hits = conn.execute(sql, [query, query, *values, limit]).fetchall()
            facets = self.facets(conn, where, values)

        return {
            "engine": self.engine,
            "hits": [self._format_hit(row) for row in hits],
            "facets": facets,
            "total": len(hits),
        }

    def facets(self, conn: psycopg.Connection, where: str, values: list[Any]) -> dict[str, Any]:
        brand_sql = f"SELECT brand AS value, count(*) AS count FROM products WHERE {where} GROUP BY brand ORDER BY count DESC LIMIT 10"
        category_sql = f"SELECT category AS value, count(*) AS count FROM products WHERE {where} GROUP BY category ORDER BY count DESC LIMIT 10"
        return {
            "brands": conn.execute(brand_sql, values).fetchall(),
            "categories": conn.execute(category_sql, values).fetchall(),
        }

    def review_analytics(self) -> dict[str, Any]:
        with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT count(*) AS review_count,
                       round(avg(rating)::numeric, 2) AS avg_rating,
                       coalesce(sum(helpful_vote), 0) AS helpful_votes
                FROM reviews
                """
            ).fetchone()
        return dict(row or {})

    def _format_hit(self, row: dict[str, Any]) -> dict[str, Any]:
        hit = dict(row)
        hit["highlights"] = {
            "title": [hit.pop("title_highlight")],
            "description": [hit.pop("description_highlight")],
        }
        return hit
