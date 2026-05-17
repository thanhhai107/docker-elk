from __future__ import annotations

import os
from html import unescape
from typing import Any

import requests
import streamlit as st
from streamlit_searchbox import st_searchbox


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
    ("scenario-1-product-search", "Scenario 1: Product Search"),
    ("scenario-2-review-search", "Scenario 2: Review Search"),
    ("scenario-3-analytics-aggregation", "Scenario 3: Analytics & Aggregation"),
]
FEATURES = [
    ("feature-elasticsearch-semantic-search", "Feature: Elasticsearch Semantic Search"),
]
FEATURE_IDS = {feature_id for feature_id, _label in FEATURES}
EXPERIENCES = SCENARIOS + FEATURES
FEATURE_TITLES = dict(FEATURES)

SERVICE_ACTIVITIES: dict[str, dict[str, list[str]]] = {
    "scenario-1-product-search": {
        "elasticsearch": [
            "Target: product index.",
            "Boosted fuzzy multi_match over title, brand, category, features, description, and review_text.",
            "Uses fuzziness AUTO so typos still match.",
            "Returns highlights and field-level ranking control.",
        ],
        "meilisearch": [
            "Target: product index.",
            "Runs default keyword search with built-in typo tolerance.",
            "Returns highlights with simpler ranking knobs.",
            "Less per-field boosting than Elasticsearch.",
        ],
        "postgres": [
            "Target: products table.",
            "Converts the query with websearch_to_tsquery.",
            "Searches products.search_vector with default PostgreSQL FTS.",
            "Typo-heavy queries can miss because pg_trgm is not enabled here.",
        ],
    },
    "scenario-2-review-search": {
        "elasticsearch": [
            "Target: review index.",
            "multi_match over title and text with fuzziness AUTO.",
            "Filters reviews by rating and sorts by helpful_vote desc.",
            "Returns highlighted review snippets in one request.",
        ],
        "meilisearch": [
            "Target: review index.",
            "Searches review title/text with typo tolerance.",
            "Filters by rating, sorts by helpful_vote desc.",
            "Returns highlights with simpler ranking knobs than Elasticsearch.",
        ],
        "postgres": [
            "Target: reviews table joined with products.",
            "Uses review_vector + ts_headline for snippets.",
            "Filters by rating, sorts by score then helpful_vote.",
            "Manual SQL for filtering, ranking, and snippet generation.",
        ],
    },
    "scenario-3-analytics-aggregation": {
        "elasticsearch": [
            "Target: review index.",
            "Runs size=0 analytics queries instead of returning product hits.",
            "Searches matching reviews and filters rating <= 2.",
            "Aggregates by brand, category, rating distribution, average rating, and helpful votes.",
        ],
        "meilisearch": [
            "Target: review index.",
            "Searches matching reviews with rating <= 2.",
            "Returns simple facets for brand/category/rating.",
            "Computes average rating and helpful-vote metrics in the application layer.",
        ],
        "postgres": [
            "Target: reviews table joined with products.",
            "Uses review full-text search, JOIN, and GROUP BY.",
            "Runs multiple SQL statements for brand, category, rating distribution, and summary metrics.",
        ],
    },
    "feature-elasticsearch-semantic-search": {
        "elasticsearch": [
            "Target: product index.",
            "Embeds the query with Vertex AI text-embedding-004.",
            "Runs BM25 multi_match together with KNN vector search on title_embedding.",
            "Returns one Elasticsearch-ranked result set from lexical and vector evidence.",
        ],
    },
}


def get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(f"{BACKEND_URL}{path}", params=params, timeout=120)
    response.raise_for_status()
    return response.json()


def request_error_detail(exc: requests.RequestException) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    try:
        payload = response.json()
    except ValueError:
        return str(exc)
    detail = payload.get("detail")
    if detail:
        return str(detail)
    return str(exc)


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

    capability = result.get("semantic_capability")
    if capability:
        with st.expander("Vector search capability", expanded=False):
            label_map = {
                "semantic_config": "Configuration",
                "model_source": "Model source",
                "vector_field": "Vector field",
                "vector_dimensions": "Vector dimensions",
                "combination": "Combination",
                "conclusion": "Conclusion",
            }
            for field, label in label_map.items():
                value = capability.get(field)
                if value:
                    st.markdown(f"**{label}:** {value}")

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
    st.markdown("## Output")
    results = data["results"]
    if len(results) == 1:
        render_result(results[0])
        return

    cols = st.columns(len(results))
    for col, result in zip(cols, results):
        with col:
            render_result(result)


def run_search(experience_id: str, selected_query: str | None, limit: int, engine: str) -> None:
    try:
        params: dict[str, Any] = {"limit": limit}
        if selected_query:
            params["q"] = selected_query
        if experience_id in FEATURE_IDS:
            result = get_json("/features/elasticsearch/semantic-search", params)
            data = {
                "scenario_id": experience_id,
                "title": FEATURE_TITLES[experience_id],
                "flow_name": "Elasticsearch Semantic Search",
                "query": selected_query or "",
                "queries": {"query": selected_query or ""},
                "engine": "elasticsearch",
                "winner": None,
                "winner_reason": None,
                "results": [result],
            }
        else:
            params["engine"] = engine
            data = get_json(f"/scenarios/{experience_id}", params)
    except requests.RequestException as exc:
        st.error(f"Backend is not ready: {request_error_detail(exc)}")
        return

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

experience_labels = {label: experience_id for experience_id, label in EXPERIENCES}
service_labels = {label: engine for engine, label in SERVICE_LABELS.items()}

if "search_request" not in st.session_state:
    st.session_state.search_request = None

if "selected_query" not in st.session_state:
    st.session_state.selected_query = ""

def suggestion_search(prefix: str) -> list[tuple[str, str]]:
    return [(title, title) for title in fetch_suggestions(prefix, limit=8)]

st.markdown("## Input")
scenario_col, service_col, limit_col, button_col = st.columns([3.6, 3.0, 1.6, 1.2])
with scenario_col:
    selected_label = st.selectbox("Scenario / Feature", list(experience_labels), key="experience_label")
selected_experience_id = experience_labels[selected_label]
available_service_labels = (
    {"Elasticsearch": "elasticsearch"}
    if selected_experience_id in FEATURE_IDS
    else service_labels
)
if st.session_state.get("service_label") not in available_service_labels:
    st.session_state.service_label = next(iter(available_service_labels))
with service_col:
    st.selectbox(
        "Service",
        list(available_service_labels),
        key="service_label",
        disabled=selected_experience_id in FEATURE_IDS,
    )
with limit_col:
    limit = st.number_input("Top results", min_value=3, max_value=20, value=10, step=1, key="limit_value")
with button_col:
    st.markdown("<div style='height:1.72rem'></div>", unsafe_allow_html=True)
    submitted = st.button("🔍", use_container_width=True, key="search_button", help="Run search")

selected_suggestion = st_searchbox(
    suggestion_search,
    placeholder="Type product, review problem, or analytics keyword",
    label="Search query",
    key="query_box",
    clear_on_submit=False,
    edit_after_submit="current",
)

auto_run = False
if isinstance(selected_suggestion, str) and selected_suggestion and selected_suggestion != st.session_state.selected_query:
    st.session_state.selected_query = selected_suggestion
    auto_run = True

if submitted or auto_run:
    typed_query = ""
    box_value = st.session_state.get("query_box")
    if isinstance(box_value, dict):
        typed_query = (box_value.get("search") or "").strip()
    if not typed_query and isinstance(selected_suggestion, str):
        typed_query = selected_suggestion.strip()
    if not typed_query:
        typed_query = (st.session_state.selected_query or "").strip()
    if not typed_query:
        st.warning("Please enter a query before searching.")
        st.session_state.search_request = None
    else:
        st.session_state.selected_query = typed_query
        st.session_state.search_request = {
            "experience_id": experience_labels[st.session_state.experience_label],
            "query": typed_query,
            "limit": int(st.session_state.limit_value),
            "engine": available_service_labels[st.session_state.service_label],
        }

if st.session_state.search_request:
    request = st.session_state.search_request
    request_experience_id = request.get("experience_id") or request.get("scenario_id")
    if not request_experience_id:
        st.error("Search request is missing a scenario or feature.")
    else:
        run_search(
            request_experience_id,
            request["query"],
            request["limit"],
            request.get("engine", "all"),
        )
else:
    st.info("Enter a query, choose a scenario, then press Search.")
