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
    ("scenario-1-full-text-keyword-search", "Scenario 1: Full-text/Keyword Search"),
    ("scenario-2-semantic-search", "Scenario 2: Intent-Aware Search"),
    ("scenario-3-analytics-aggregation", "Scenario 3: Analytics & Aggregation"),
]

SERVICE_ACTIVITIES: dict[str, dict[str, list[str]]] = {
    "scenario-1-full-text-keyword-search": {
        "elasticsearch": [
            "Targets: product index and review index.",
            "Runs boosted fuzzy multi_match over title, brand, category, features, description, and review_text.",
            "Uses fuzziness for wireles, canclling, and headphnes.",
            "Runs a second review evidence query with rating <= 2, highlights, and helpful_vote tie-breaks.",
        ],
        "meilisearch": [
            "Targets: product index and review index.",
            "Runs Meilisearch keyword search over title, brand, category, features, description, and review_text.",
            "Uses Meilisearch built-in typo tolerance and ranking rules.",
            "Runs review filtering/highlighting, with less field-level ranking control than Elasticsearch.",
        ],
        "postgres": [
            "Targets: products table and reviews table.",
            "Converts the query with websearch_to_tsquery.",
            "Searches products.search_vector with default PostgreSQL FTS.",
            "Uses ts_headline for review evidence, but typo-heavy product terms can miss without pg_trgm.",
        ],
    },
    "scenario-2-semantic-search": {
        "elasticsearch": [
            "Target: product index.",
            "Expands the query with the synonym_graph filter (anc/headphones/wireless/cheap).",
            "Runs boosted multi_match across title, features, brand, category, description, review_text.",
            "Captures user intent without external embedding providers.",
        ],
        "meilisearch": [
            "Target: product index.",
            "No curated synonyms or embeddings are configured here.",
            "Runs normal full-text search only.",
            "Shows the remaining baseline when intent expansion is not configured.",
        ],
        "postgres": [
            "Target: products table.",
            "PostgreSQL core has no built-in synonym/embedding generator.",
            "Runs products.search_vector full-text search.",
            "No vector extension or synonym dictionary is used here.",
        ],
    },
    "scenario-3-analytics-aggregation": {
        "elasticsearch": [
            "Target: review index.",
            "Runs size=0 analytics queries instead of returning product hits.",
            "Searches battery problem reviews and filters rating <= 2.",
            "Aggregates by brand, category, rating distribution, average rating, and helpful votes.",
        ],
        "meilisearch": [
            "Target: review index.",
            "Searches battery problem reviews with rating <= 2.",
            "Returns simple facets for brand/category/rating.",
            "Computes average rating and helpful-vote metrics in the application layer.",
        ],
        "postgres": [
            "Target: reviews table joined with products.",
            "Uses review full-text search, JOIN, and GROUP BY.",
            "Runs multiple SQL statements for brand, category, rating distribution, and summary metrics.",
        ],
    },
}


def get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(f"{BACKEND_URL}{path}", params=params, timeout=120)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=10, show_spinner=False)
def fetch_suggestions(prefix: str, limit: int = 5) -> list[str]:
    if not prefix or len(prefix) < 2:
        return []
    try:
        data = get_json("/search/elasticsearch/as-you-type", {"q": prefix, "limit": limit})
    except requests.RequestException:
        return []
    titles: list[str] = []
    for hit in data.get("hits", []):
        title = hit.get("title")
        if title and title not in titles:
            titles.append(title)
    return titles[:limit]


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
                f"Product: {hit.get('product_title', 'n/a')}",
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

    if result.get("semantic_capability"):
        with st.expander("Semantic capability", expanded=result.get("document_type") == "product"):
            st.json(result["semantic_capability"])

    aggregations = result.get("aggregations") or {}
    if aggregations:
        with st.expander("Aggregation / Facet", expanded=result.get("document_type") == "analytics"):
            st.json(aggregations)

    sections = result.get("sections") or []
    if sections:
        for section in sections:
            st.markdown(f"#### {section.get('title', 'Result section')}")
            st.caption(
                " | ".join(
                    [
                        f"Query: `{section.get('query', '')}`",
                        f"Hits: {section.get('total', 0)}",
                    ]
                )
            )
            hits = section.get("hits", [])
            if not hits:
                st.info("No result documents returned for this section.")
                continue
            for hit in hits[:5]:
                render_hit(hit, section.get("document_type", "product"))
        return

    hits = result.get("hits", [])
    if not hits and result.get("document_type") != "analytics":
        st.info("No result documents returned for this service.")
        return

    for hit in hits[:5]:
        render_hit(hit, result.get("document_type", "product"))


def render_service_activities(scenario_id: str, data: dict[str, Any]) -> None:
    st.markdown("## 2. Service Execution Flow")
    queries = data.get("queries") or {"query": data["query"]}
    if len(queries) == 1:
        st.markdown(f"**Query:** `{next(iter(queries.values()))}`")
    else:
        st.markdown("**Queries:**")
        for name, value in queries.items():
            st.markdown(f"- `{name}`: `{value}`")
    st.markdown(f"**Scenario:** {data.get('title', '')}")
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
    div[data-testid="stHorizontalBlock"] div[data-testid="column"]:has(> div > div > div > button[kind="primary"]) > div {
        padding-top: 1.72rem;
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

if "query_value" not in st.session_state:
    st.session_state.query_value = ""
if "trigger_search" not in st.session_state:
    st.session_state.trigger_search = False

st.markdown("## 1. Input & Search Options")
search_col, scenario_col, service_col, limit_col, button_col = st.columns([4.4, 2.4, 1.9, 1.1, 1])
with search_col:
    query = st.text_input(
        "Search query",
        key="query_value",
        placeholder="Type your product need, review problem, or analytics keyword",
    )
with scenario_col:
    selected_label = st.selectbox("Scenario", list(scenario_labels), key="scenario_label")
with service_col:
    selected_service_label = st.selectbox("Service", list(service_labels), key="service_label")
with limit_col:
    limit = st.number_input("Top results", min_value=3, max_value=20, value=10, step=1, key="limit_value")
with button_col:
    submitted = st.button("Search", use_container_width=True, key="search_button")

suggestions = fetch_suggestions(query.strip()) if query else []
if suggestions:
    st.caption("Suggestions (search-as-you-type)")
    suggestion_cols = st.columns(len(suggestions))
    for index, (sug_col, suggestion) in enumerate(zip(suggestion_cols, suggestions)):
        with sug_col:
            if st.button(suggestion, key=f"suggestion_{index}", use_container_width=True):
                st.session_state.query_value = suggestion
                st.session_state.trigger_search = True
                st.rerun()

if submitted or st.session_state.trigger_search:
    st.session_state.trigger_search = False
    cleaned_query = st.session_state.query_value.strip()
    st.session_state.search_request = {
        "scenario_id": scenario_labels[st.session_state.scenario_label],
        "query": cleaned_query or None,
        "limit": int(st.session_state.limit_value),
        "engine": service_labels[st.session_state.service_label],
    }

if st.session_state.search_request:
    request = st.session_state.search_request
    run_search(request["scenario_id"], request["query"], request["limit"], request.get("engine", "all"))
else:
    st.info("Enter a query, choose a scenario, then press Search.")
