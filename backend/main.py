from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query

from backend.services.cluster_control_service import ElasticsearchClusterControlService
from backend.services.cluster_control_service import NodeAction
from backend.services.cluster_status_service import ElasticsearchClusterStatusService
from backend.services.elasticsearch_service import ElasticsearchSearchService
from backend.services.meilisearch_service import MeiliSearchService
from backend.services.postgres_service import PostgresSearchService
from backend.services.workflow_service import SCENARIOS, WorkflowService


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


@app.get("/search/elasticsearch/as-you-type")
def elasticsearch_as_you_type(
    q: str = Query("sony wh"),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    return ElasticsearchSearchService().search_as_you_type(q, limit)


@app.get("/search/elasticsearch/semantic")
def elasticsearch_semantic(
    q: str = Query("headphones for flights with quiet cabin noise"),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    return run_elasticsearch_semantic_search(q, limit)


@app.get("/features/elasticsearch/semantic-search")
def elasticsearch_semantic_feature(
    q: str = Query("headphones for flights with quiet cabin noise"),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    return run_elasticsearch_semantic_search(q, limit)


@app.get("/features/elasticsearch/cluster-status")
def elasticsearch_cluster_status_feature() -> dict[str, Any]:
    return ElasticsearchClusterStatusService().snapshot()


@app.get("/features/elasticsearch/cluster-control")
def elasticsearch_cluster_control_config() -> dict[str, Any]:
    return ElasticsearchClusterControlService().config()


@app.post("/features/elasticsearch/cluster-control/{target_id}/{action}")
def elasticsearch_cluster_control_action(target_id: str, action: NodeAction) -> dict[str, Any]:
    try:
        result = ElasticsearchClusterControlService().run(target_id, action)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown Elasticsearch target: {target_id}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result)
    return result


def run_elasticsearch_semantic_search(q: str, limit: int) -> dict[str, Any]:
    try:
        return ElasticsearchSearchService().semantic_search(q, limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@app.get("/scenarios")
def list_scenarios() -> dict[str, Any]:
    scenarios = [{"id": scenario_id, **metadata} for scenario_id, metadata in SCENARIOS.items()]
    return {"scenarios": scenarios}


@app.get("/features")
def list_features() -> dict[str, Any]:
    return {
        "features": [
            {
                "id": "feature-elasticsearch-semantic-search",
                "title": "Feature: Elasticsearch Semantic Search",
                "engine": "elasticsearch",
                "path": "/features/elasticsearch/semantic-search",
            },
            {
                "id": "feature-elasticsearch-cluster-resilience",
                "title": "Feature: Elasticsearch Cluster Resilience",
                "engine": "elasticsearch",
                "path": "/features/elasticsearch/cluster-status",
            },
        ]
    }


@app.get("/scenarios/{scenario_id}")
def run_scenario(
    scenario_id: str,
    q: str | None = None,
    limit: int = Query(10, ge=1, le=50),
    engine: Literal["all", "elasticsearch", "meilisearch", "postgres"] = "all",
) -> dict[str, Any]:
    try:
        return WorkflowService().run(scenario_id, q, limit, engine)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
