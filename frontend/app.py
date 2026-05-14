from __future__ import annotations

import os
from html import unescape
from typing import Any

import requests
import streamlit as st


BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
ENGINE_LABELS = {
    "elasticsearch": "Elasticsearch",
    "meilisearch": "Meilisearch",
    "postgres": "PostgreSQL FTS",
}
SCENARIO_TABS = [
    ("act-1-product-discovery", "ACT 1: Product Discovery Search"),
    ("act-2-review-deep-search", "ACT 2: Review Deep Search"),
    ("act-3-review-analytics", "ACT 3: Review Analytics & Aggregation"),
    ("act-4-hybrid-recommendation", "ACT 4: Hybrid / Semantic Recommendation"),
    ("act-5-scale-readiness", "ACT 5: Worker Failover"),
]


def get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(f"{BACKEND_URL}{path}", params=params, timeout=120)
    response.raise_for_status()
    return response.json()


def clean_highlight(value: str) -> str:
    return unescape(value or "").replace("<em>", "<mark>").replace("</em>", "</mark>")


def render_hit(hit: dict[str, Any], document_type: str) -> None:
    highlights = hit.get("highlights", {})
    title = clean_highlight((highlights.get("title") or [hit.get("title", "")])[0])
    body_key = "text" if document_type == "review" else "description"
    body = clean_highlight((highlights.get(body_key) or [hit.get(body_key, "")])[0])

    st.markdown(f"**{title or hit.get('product_id') or hit.get('review_id')}**", unsafe_allow_html=True)
    if document_type == "review":
        st.caption(
            f"Product {hit.get('product_id', '')} | rating {hit.get('rating', 0)} | "
            f"helpful {hit.get('helpful_vote', 0)} | score {hit.get('score')}"
        )
    else:
        rating = hit.get("average_rating", hit.get("rating", 0))
        reviews = hit.get("rating_number", hit.get("review_count", 0))
        st.caption(
            f"{hit.get('brand', 'Unknown')} | {hit.get('category', 'Electronics')} | "
            f"${float(hit.get('price') or 0):,.2f} | rating {rating} | reviews {reviews} | "
            f"score {hit.get('score')}"
        )
    if body:
        st.markdown(body, unsafe_allow_html=True)


def render_result(result: dict[str, Any]) -> None:
    label = ENGINE_LABELS.get(result["engine"], result["engine"])
    st.subheader(label)
    if result.get("error"):
        st.error(result["error"])
        return

    m1, m2, m3 = st.columns(3)
    m1.metric("Time", f"{result.get('took_ms', 0)} ms")
    m2.metric("Requests", result.get("number_of_requests", 0))
    m3.metric("Hits", result.get("total", 0))

    st.caption(
        f"Highlight: {'yes' if result.get('has_highlight') else 'no'} | "
        f"Aggregation: {'yes' if result.get('has_aggregation') else 'no'} | "
        f"Custom ranking: {'yes' if result.get('has_custom_ranking') else 'no'} | "
        f"Backend: {result.get('backend_complexity')}"
    )
    st.write(result.get("note", ""))

    aggregations = result.get("aggregations") or {}
    if aggregations:
        with st.expander("Aggregation / Facet", expanded=False):
            st.json(aggregations)

    for hit in result.get("hits", [])[:5]:
        render_hit(hit, result.get("document_type", "product"))
        st.divider()


def render_scenario(scenario_id: str, selected_query: str | None, limit: int) -> None:
    try:
        params: dict[str, Any] = {"limit": limit}
        if selected_query:
            params["q"] = selected_query
        data = get_json(f"/scenarios/{scenario_id}", params)
    except requests.RequestException as exc:
        st.error(f"Backend is not ready: {exc}")
        return

    st.markdown(f"**Query:** `{data['query']}`")
    st.caption(data.get("summary", ""))
    if data.get("winner_reason"):
        st.info(f"Winner: Elasticsearch. {data['winner_reason']}")
    cols = st.columns(3)
    for col, result in zip(cols, data["results"]):
        with col:
            render_result(result)


def render_benchmark(limit: int) -> None:
    try:
        data = get_json("/workflow-benchmark", {"limit": limit})
    except requests.RequestException as exc:
        st.error(f"Backend is not ready: {exc}")
        return

    rows = []
    for item in data["rows"]:
        engines = item["engines"]
        rows.append(
            {
                "Workflow": item["workflow"],
                "Query": item["query"],
                "Elasticsearch": describe_engine(engines.get("elasticsearch", {})),
                "Meilisearch": describe_engine(engines.get("meilisearch", {})),
                "PostgreSQL FTS": describe_engine(engines.get("postgres", {})),
                "Winner": item["winner"],
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    cols = st.columns(3)
    for engine, col in zip(["elasticsearch", "meilisearch", "postgres"], cols):
        with col:
            st.subheader(ENGINE_LABELS[engine])
            engine_rows = [
                {
                    "workflow": row["workflow"],
                    **row["engines"].get(engine, {}),
                }
                for row in data["rows"]
            ]
            st.dataframe(engine_rows, use_container_width=True, hide_index=True)


def describe_engine(engine: dict[str, Any]) -> str:
    if not engine:
        return "n/a"
    return (
        f"{engine.get('number_of_requests')} req, "
        f"{engine.get('total_workflow_time_ms')} ms, "
        f"score {engine.get('score')}/5"
    )


st.set_page_config(page_title="Amazon Electronics Search Demo", layout="wide")
st.title("Amazon Electronics Search Demo")

scenario_labels = {label: scenario_id for scenario_id, label in SCENARIO_TABS}

if "search_request" not in st.session_state:
    st.session_state.search_request = None

with st.form("search_form"):
    search_col, act_col, limit_col, button_col = st.columns([5, 2.4, 1.2, 1])
    with search_col:
        query = st.text_input("Search query", placeholder="Type your product need, review problem, or analytics keyword")
    with act_col:
        selected_label = st.selectbox("Act", list(scenario_labels))
    with limit_col:
        limit = st.number_input("Top results", min_value=3, max_value=20, value=10, step=1)
    with button_col:
        st.write("")
        submitted = st.form_submit_button("Search", use_container_width=True)

if submitted:
    selected_scenario = scenario_labels[selected_label]
    cleaned_query = query.strip()
    if not cleaned_query and selected_scenario not in {"act-3-review-analytics", "act-5-scale-readiness"}:
        st.warning("Enter a query before searching this ACT.")
    else:
        st.session_state.search_request = {
            "scenario_id": selected_scenario,
            "query": cleaned_query or None,
            "limit": int(limit),
        }

if st.session_state.search_request:
    request = st.session_state.search_request
    render_scenario(request["scenario_id"], request["query"], request["limit"])
else:
    st.info("Enter a query, choose an ACT, then press Search.")

with st.expander("Benchmark Report", expanded=False):
    render_benchmark(10)
