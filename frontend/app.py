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
SERVICE_LABELS = {
    "all": "All services",
    **ENGINE_LABELS,
}
SCENARIOS = [
    ("act-1-product-discovery", "ACT 1: Keyword Product Search"),
    ("act-2-review-deep-search", "ACT 2: Review Deep Search"),
    ("act-3-review-analytics", "ACT 3: Review Analytics & Aggregation"),
    ("act-4-hybrid-recommendation", "ACT 4: Semantic Recommendation"),
]

SERVICE_ACTIVITIES: dict[str, dict[str, list[str]]] = {
    "act-1-product-discovery": {
        "elasticsearch": [
            "Target: product index.",
            "Runs boosted multi_match over title, brand, category, features, description, and review_text.",
            "Uses fuzziness to tolerate typos and near matches.",
            "Ranks title/features matches higher than description/review_text matches.",
        ],
        "meilisearch": [
            "Target: product index.",
            "Runs Meilisearch keyword search over title, brand, category, features, description, and review_text.",
            "Applies a lightweight product filter.",
            "Uses Meilisearch built-in typo tolerance and ranking rules.",
        ],
        "postgres": [
            "Target: products table.",
            "Converts the query with websearch_to_tsquery.",
            "Searches products.search_vector and adds trigram similarity on title/description.",
            "Ranks by full-text score plus similarity.",
        ],
    },
    "act-2-review-deep-search": {
        "elasticsearch": [
            "Target: review index.",
            "Searches logical review_title and review_text fields.",
            "Routes positive queries to rating >= 4 and negative queries to rating <= 3.",
            "Highlights matching review snippets and sorts by relevance then helpful_vote.",
        ],
        "meilisearch": [
            "Target: review index.",
            "Searches review title/text with highlight enabled.",
            "Applies the same positive/negative rating filter.",
            "Sorts matching reviews by helpful_vote.",
        ],
        "postgres": [
            "Target: reviews table joined with products.",
            "Searches reviews.review_vector built from title and text.",
            "Applies the same rating filter.",
            "Uses ts_headline for snippets and sorts by text rank then helpful_vote.",
        ],
    },
    "act-3-review-analytics": {
        "elasticsearch": [
            "Target: review index.",
            "Runs size=0 analytics queries instead of returning product hits.",
            "Aggregates by brand, category, and rating.",
            "Combines text filters such as overheating/battery with aggregations in the same engine.",
        ],
        "meilisearch": [
            "Target: review index.",
            "Fetches matching review documents from Meilisearch.",
            "Aggregates brand/category/rating metrics in the application layer.",
            "Used as a fallback because nested analytics are limited compared with Elasticsearch.",
        ],
        "postgres": [
            "Target: reviews table joined with products.",
            "Uses SQL GROUP BY for brand, category, and rating distribution.",
            "Uses review full-text search for topic filters.",
            "Runs multiple SQL statements for separate analytics questions.",
        ],
    },
    "act-4-hybrid-recommendation": {
        "elasticsearch": [
            "Target: product index.",
            "Expands natural-language intent into related search terms.",
            "Searches title, brand, category, features, description, and review_text with boosts.",
            "Uses function_score with rating, review count, and helpful votes.",
        ],
        "meilisearch": [
            "Target: product index.",
            "Searches an expanded natural-language query.",
            "Filters for products with acceptable average rating.",
            "Sorts by average_rating and rating_number as recommendation signals.",
        ],
        "postgres": [
            "Target: products table.",
            "Searches the expanded query through products.search_vector.",
            "Combines full-text rank with average_rating and rating_number.",
            "Returns products ordered by the computed recommendation score.",
        ],
    },
}


def get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(f"{BACKEND_URL}{path}", params=params, timeout=120)
    response.raise_for_status()
    return response.json()


def clean_highlight(value: Any) -> str:
    return unescape(str(value or "")).replace("<em>", "<mark>").replace("</em>", "</mark>")


def first_text(hit: dict[str, Any], keys: list[str], fallback_keys: list[str]) -> str:
    highlights = hit.get("highlights", {})
    for key in keys:
        value = highlights.get(key)
        if value:
            return clean_highlight(value[0])
    for key in fallback_keys:
        value = hit.get(key)
        if value:
            return clean_highlight(value)
    return ""


def format_score(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def format_price(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def render_product_hit(hit: dict[str, Any]) -> None:
    title = first_text(hit, ["title"], ["title"]) or hit.get("product_id", "Untitled product")
    review = first_text(hit, ["review_text"], ["review_text"])
    description = first_text(hit, ["description"], ["description"])

    st.markdown(f"#### {title}", unsafe_allow_html=True)
    st.caption(
        " | ".join(
            [
                f"Product ID: {hit.get('product_id', 'n/a')}",
                f"Brand: {hit.get('brand', 'Unknown')}",
                f"Category: {hit.get('category', 'Electronics')}",
                f"Price: {format_price(hit.get('price'))}",
                f"Rating: {hit.get('average_rating', hit.get('rating', 0))}",
                f"Reviews: {hit.get('rating_number', hit.get('review_count', 0))}",
                f"Score: {format_score(hit.get('score'))}",
            ]
        )
    )

    if review:
        st.markdown("**Review evidence**")
        st.markdown(review, unsafe_allow_html=True)
    if description:
        with st.expander("Description", expanded=not review):
            st.markdown(description, unsafe_allow_html=True)
    if hit.get("features"):
        with st.expander("Features", expanded=False):
            st.write(hit.get("features"))
    with st.expander("Full document fields", expanded=False):
        st.json(hit)


def render_review_hit(hit: dict[str, Any]) -> None:
    title = first_text(hit, ["review_title", "title"], ["review_title", "title"]) or hit.get("review_id", "Untitled review")
    review = first_text(hit, ["review_text", "text"], ["review_text", "text"])

    st.markdown(f"#### {title}", unsafe_allow_html=True)
    st.caption(
        " | ".join(
            [
                f"Review ID: {hit.get('review_id', 'n/a')}",
                f"Product ID: {hit.get('product_id', 'n/a')}",
                f"Brand: {hit.get('brand', 'Unknown')}",
                f"Category: {hit.get('category', 'Electronics')}",
                f"Rating: {hit.get('rating', 0)}",
                f"Helpful votes: {hit.get('helpful_vote', 0)}",
                f"Verified: {hit.get('verified_purchase', False)}",
                f"Score: {format_score(hit.get('score'))}",
            ]
        )
    )

    if review:
        st.markdown("**Review**")
        st.markdown(review, unsafe_allow_html=True)
    with st.expander("Full review fields", expanded=False):
        st.json(hit)


def render_hit(hit: dict[str, Any], document_type: str) -> None:
    with st.container(border=True):
        if document_type == "review":
            render_review_hit(hit)
        else:
            render_product_hit(hit)


def render_result(result: dict[str, Any]) -> None:
    label = ENGINE_LABELS.get(result["engine"], result["engine"])
    st.markdown(f"### {label}")
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
        f"Backend complexity: {result.get('backend_complexity')}"
    )
    st.write(result.get("note", ""))

    aggregations = result.get("aggregations") or {}
    if aggregations:
        with st.expander("Aggregation / Facet", expanded=result.get("document_type") == "analytics"):
            st.json(aggregations)

    hits = result.get("hits", [])
    if not hits and result.get("document_type") != "analytics":
        st.info("No result documents returned for this service.")
        return

    for hit in hits[:5]:
        render_hit(hit, result.get("document_type", "product"))


def render_service_activities(scenario_id: str, data: dict[str, Any]) -> None:
    st.markdown("## 2. Service Execution Flow")
    st.markdown(f"**Query:** `{data['query']}`")
    st.markdown(f"**Act:** {data.get('title', '')}")
    st.caption(data.get("summary", ""))

    results = data.get("results", [])
    if len(results) == 1:
        engine = results[0]["engine"]
        with st.container(border=True):
            st.markdown(f"#### {ENGINE_LABELS.get(engine, engine)}")
            for step in SERVICE_ACTIVITIES.get(scenario_id, {}).get(engine, []):
                st.markdown(f"- {step}")
        return

    cols = st.columns(len(results))
    for col, result in zip(cols, results):
        engine = result["engine"]
        with col:
            with st.container(border=True):
                st.markdown(f"#### {ENGINE_LABELS.get(engine, engine)}")
                for step in SERVICE_ACTIVITIES.get(scenario_id, {}).get(engine, []):
                    st.markdown(f"- {step}")


def render_output(data: dict[str, Any]) -> None:
    st.markdown("## 3. Output")
    results = data["results"]
    if len(results) == 1:
        render_result(results[0])
        return

    cols = st.columns(len(results))
    for col, result in zip(cols, results):
        with col:
            render_result(result)


def run_search(scenario_id: str, selected_query: str | None, limit: int, engine: str) -> None:
    try:
        params: dict[str, Any] = {"limit": limit, "engine": engine}
        if selected_query:
            params["q"] = selected_query
        data = get_json(f"/scenarios/{scenario_id}", params)
    except requests.RequestException as exc:
        st.error(f"Backend is not ready: {exc}")
        return

    render_service_activities(scenario_id, data)
    render_output(data)


st.set_page_config(page_title="Amazon Electronics Search Demo", layout="wide")
st.markdown(
    """
    <style>
    div[data-testid="stFormSubmitButton"] {
        padding-top: 1.72rem;
    }
    div[data-testid="stForm"] button[kind="primaryFormSubmit"] {
        width: 100%;
    }
    mark {
        background-color: #fff176;
        color: #111827;
        padding: 0 0.12rem;
        border-radius: 0.15rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("Amazon Electronics Search Demo")

scenario_labels = {label: scenario_id for scenario_id, label in SCENARIOS}
service_labels = {label: engine for engine, label in SERVICE_LABELS.items()}

if "search_request" not in st.session_state:
    st.session_state.search_request = None

st.markdown("## 1. Input & Search Options")
with st.form("search_form", border=False):
    search_col, act_col, service_col, limit_col, button_col = st.columns([4.4, 2.4, 1.9, 1.1, 1])
    with search_col:
        query = st.text_input(
            "Search query",
            placeholder="Type your product need, review problem, or analytics keyword",
        )
    with act_col:
        selected_label = st.selectbox("Act", list(scenario_labels))
    with service_col:
        selected_service_label = st.selectbox("Service", list(service_labels))
    with limit_col:
        limit = st.number_input("Top results", min_value=3, max_value=20, value=10, step=1)
    with button_col:
        submitted = st.form_submit_button("Search", use_container_width=True)

if submitted:
    selected_scenario = scenario_labels[selected_label]
    selected_service = service_labels[selected_service_label]
    cleaned_query = query.strip()
    if not cleaned_query and selected_scenario != "act-3-review-analytics":
        st.warning("Enter a query before searching this ACT.")
    else:
        st.session_state.search_request = {
            "scenario_id": selected_scenario,
            "query": cleaned_query or None,
            "limit": int(limit),
            "engine": selected_service,
        }

if st.session_state.search_request:
    request = st.session_state.search_request
    run_search(request["scenario_id"], request["query"], request["limit"], request.get("engine", "all"))
else:
    st.info("Enter a query, choose an ACT, then press Search.")
