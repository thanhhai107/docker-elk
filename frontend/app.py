from __future__ import annotations

import os
import random
from html import unescape
from typing import Any

import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from streamlit_searchbox import st_searchbox


BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
CLUSTER_REFRESH_INTERVAL_MS = 5000
SEMANTIC_FEATURE_ID = "feature-elasticsearch-semantic-search"
CLUSTER_FEATURE_ID = "feature-elasticsearch-cluster-resilience"

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
    (SEMANTIC_FEATURE_ID, "Feature: Elasticsearch Semantic Search"),
    (CLUSTER_FEATURE_ID, "Feature: Elasticsearch Cluster Resilience"),
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


def post_json(path: str) -> dict[str, Any]:
    response = requests.post(f"{BACKEND_URL}{path}", timeout=120)
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
    if isinstance(detail, dict):
        lines = []
        if detail.get("stderr"):
            lines.append(f"stderr: {detail['stderr']}")
        if detail.get("stdout"):
            lines.append(f"stdout: {detail['stdout']}")
        if lines:
            return "\n".join(lines)
        return str(detail)
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


def fetch_cluster_status() -> dict[str, Any]:
    return get_json("/features/elasticsearch/cluster-status")


def fetch_cluster_control_config() -> dict[str, Any]:
    return get_json("/features/elasticsearch/cluster-control")


def run_cluster_control_action(target_id: str, action: str) -> dict[str, Any]:
    return post_json(f"/features/elasticsearch/cluster-control/{target_id}/{action}")


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
                continue
            for hit in hits[:5]:
                render_hit(hit, section.get("document_type", "product"))
        return

    hits = result.get("hits", [])
    if not hits and result.get("document_type") != "analytics":
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


def cluster_table_rows(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    return [{column: row.get(column, "") for column in columns} for row in rows]


def render_cluster_table(
    title: str,
    rows: list[dict[str, Any]],
    columns: list[str],
    *,
    expanded: bool = False,
) -> None:
    if not rows:
        return
    st.markdown(f"#### {title}")
    table_height = min(max(38 * (len(rows) + 1), 120), 900)
    st.dataframe(
        cluster_table_rows(rows, columns),
        use_container_width=True,
        hide_index=True,
        height=table_height,
    )


def format_percent(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def render_cluster_health_banner(status: str) -> None:
    label = f"Cluster health: {status.upper()}"
    if status == "green":
        st.success(label)
    elif status == "yellow":
        st.warning(label)
    elif status in {"red", "unreachable"}:
        st.error(label)
    else:
        st.info(label)


def render_allocation_explain(allocation_explain: dict[str, Any]) -> None:
    status = allocation_explain.get("status", "unknown")
    if status == "no_unassigned_shards":
        return
    expanded = status == "error"
    with st.expander("Allocation explanation", expanded=expanded):
        if status == "error":
            st.error(allocation_explain.get("message", "Allocation explain failed."))
            return
        body = allocation_explain.get("body", {})
        explanation = body.get("explanation")
        if explanation:
            st.write(explanation)
        st.json(body)


def target_is_online(target: dict[str, Any], nodes: list[dict[str, Any]]) -> bool:
    identifiers = {
        str(target.get("id") or "").lower(),
        str(target.get("host") or "").lower(),
        str(target.get("label") or "").lower(),
    }
    identifiers.discard("")
    for node in nodes:
        node_values = {
            str(node.get("name") or "").lower(),
            str(node.get("ip") or "").lower(),
            str(node.get("node") or "").lower(),
        }
        if identifiers & node_values:
            return True
    return False


def cluster_worker_rows(data: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = data.get("nodes", [])
    rows = []
    for target in config.get("targets", []):
        online = target_is_online(target, nodes)
        rows.append(
            {
                "worker": target.get("label") or target.get("id"),
                "host": target.get("host"),
                "status": "online" if online else "offline",
            }
        )
    return rows


def cluster_control_targets(
    data: dict[str, Any],
    config: dict[str, Any],
    *,
    online: bool,
) -> list[dict[str, Any]]:
    nodes = data.get("nodes", [])
    return [
        target
        for target in config.get("targets", [])
        if target_is_online(target, nodes) == online
    ]


def run_targets_cluster_action(
    selected_targets: list[dict[str, Any]],
    action: str,
) -> dict[str, Any]:
    results = []
    for target in selected_targets:
        target_id = target["id"]
        try:
            result = run_cluster_control_action(target_id, action)
        except requests.RequestException as exc:
            result = {
                "ok": False,
                "target": target_id,
                "action": action,
                "error": request_error_detail(exc),
            }
        results.append(result)

    return {
        "ok": all(result.get("ok") for result in results),
        "action": action,
        "targets": [target["id"] for target in selected_targets],
        "results": results,
    }


def run_random_stop_action(data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    candidates = cluster_control_targets(data, config, online=True)
    if not candidates:
        return {
            "ok": False,
            "action": "stop",
            "message": "No online workers are available for random stop.",
            "results": [],
        }
    count = random.randint(1, min(2, len(candidates)))
    return run_targets_cluster_action(random.sample(candidates, count), "stop")


def run_recover_action(data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    candidates = cluster_control_targets(data, config, online=False)
    if not candidates:
        return {
            "ok": False,
            "action": "start",
            "message": "No offline workers are available for recovery.",
            "results": [],
        }
    return run_targets_cluster_action(candidates, "start")


def cluster_expected_nodes(config: dict[str, Any]) -> int | None:
    targets = config.get("targets", [])
    if not targets:
        return None
    return len(targets) + 1


def cluster_is_fully_available(data: dict[str, Any], config: dict[str, Any]) -> bool:
    expected_nodes = cluster_expected_nodes(config)
    if expected_nodes is None:
        return False
    summary = data.get("summary", {})
    node_count = int(summary.get("node_count") or 0)
    worker_rows = cluster_worker_rows(data, config)
    return (
        node_count == expected_nodes
        and bool(worker_rows)
        and all(row["status"] == "online" for row in worker_rows)
    )


def cluster_control_state(data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if not config.get("configured") or not config.get("targets"):
        return {
            "configured": False,
            "mode": "Unavailable",
            "button_label": "Node control unavailable",
            "button_help": "Elasticsearch node control is not configured.",
            "disabled": True,
            "fully_available": False,
        }

    offline_targets = cluster_control_targets(data, config, online=False)
    online_targets = cluster_control_targets(data, config, online=True)
    fully_available = cluster_is_fully_available(data, config)
    if fully_available:
        return {
            "configured": True,
            "mode": "Healthy",
            "button_label": "Turn off nodes",
            "button_help": "Randomly stop one or two online worker nodes.",
            "disabled": not online_targets,
            "fully_available": True,
        }
    return {
        "configured": True,
        "mode": "Degraded mode",
        "button_label": "Turn on nodes",
        "button_help": "Start every configured worker node that is currently offline.",
        "disabled": not offline_targets,
        "fully_available": False,
    }


def render_cluster_control_action(data: dict[str, Any], config: dict[str, Any]) -> None:
    if "cluster_control_result" not in st.session_state:
        st.session_state.cluster_control_result = None

    control = cluster_control_state(data, config)
    if st.button(
        control["button_label"],
        use_container_width=True,
        key="cluster_state_action_button",
        disabled=control["disabled"],
        help=control["button_help"],
    ):
        if control["fully_available"]:
            with st.spinner("Stopping random workers..."):
                st.session_state.cluster_control_result = run_random_stop_action(data, config)
        else:
            with st.spinner("Recovering offline workers..."):
                st.session_state.cluster_control_result = run_recover_action(data, config)
        st.rerun()


def render_cluster_control_result() -> None:
    result = st.session_state.get("cluster_control_result")
    if not result:
        return
    targets_label = ", ".join(result.get("targets", [])) or result.get("target", "")
    status_message = f"{result.get('action', 'action')} {targets_label}".strip()
    if result.get("ok"):
        st.success(f"Completed: {status_message}")
    else:
        st.error(result.get("message") or f"Failed: {status_message}")
    failed_results = [item for item in result.get("results", []) if not item.get("ok")]
    if failed_results:
        with st.expander("Control errors", expanded=True):
            for item in failed_results:
                st.markdown(f"**{item.get('target', 'unknown')}**")
                error = item.get("error") or item.get("stderr") or "Unknown error"
                st.code(str(error))


def render_cluster_status(data: dict[str, Any], config: dict[str, Any]) -> None:
    summary = data.get("summary", {})
    status = str(summary.get("status") or "unknown").lower()
    node_count = int(summary.get("node_count") or 0)
    node_delta = None
    expected_nodes = cluster_expected_nodes(config)
    if expected_nodes is not None and node_count != expected_nodes:
        node_delta = f"{node_count - expected_nodes:+d} vs expected"
    worker_rows = cluster_worker_rows(data, config)
    online_workers = sum(1 for row in worker_rows if row["status"] == "online")
    worker_total = len(worker_rows)

    title_col, button_col = st.columns([5, 1.6])
    with title_col:
        st.markdown("## Cluster Status")
        st.caption(
            f"Endpoint: `{data.get('elasticsearch_url', 'n/a')}` | "
            f"Checked at: `{data.get('generated_at', 'n/a')}`"
        )
    with button_col:
        render_cluster_control_action(data, config)

    health_col, mode_col = st.columns([2, 1])
    with health_col:
        render_cluster_health_banner(status)
    with mode_col:
        control = cluster_control_state(data, config)
        if control["fully_available"]:
            st.success(f"Mode: {control['mode']}")
        elif control["configured"]:
            st.warning(f"Mode: {control['mode']}")
        else:
            st.info(f"Mode: {control['mode']}")
    render_cluster_control_result()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Nodes", node_count, delta=node_delta)
    m2.metric("Workers online", f"{online_workers}/{worker_total}" if worker_total else "n/a")
    m3.metric("Active shard %", format_percent(summary.get("active_shards_percent")))
    m4.metric("Unassigned shards", summary.get("unassigned_shards", 0))

    m5, m6, m7, m8 = st.columns(4)
    m5.metric("Data nodes", summary.get("data_node_count", 0))
    m6.metric("Demo shards", summary.get("demo_shard_count", 0))
    m7.metric("Relocating", summary.get("relocating_shards", 0))
    m8.metric("Recovery active", summary.get("recovery_active", 0))

    nodes_tab, shards_tab, allocation_tab, recovery_tab = st.tabs(
        ["Nodes", "Shards", "Allocation", "Recovery"]
    )
    with nodes_tab:
        if worker_rows:
            st.markdown("#### Worker control targets")
            st.dataframe(worker_rows, use_container_width=True, hide_index=True)
        render_cluster_table(
            "Cluster nodes",
            data.get("nodes", []),
            ["name", "node.role", "master", "ip"],
            expanded=True,
        )
    with shards_tab:
        render_cluster_table(
            "Amazon Electronics shards",
            data.get("shards", []),
            ["index", "shard", "prirep", "state", "docs", "store", "node", "unassigned.reason"],
            expanded=True,
        )
    with allocation_tab:
        render_cluster_table(
            "Disk and shard allocation",
            data.get("allocation", []),
            ["node", "shards", "disk.indices", "disk.used", "disk.avail", "disk.total", "disk.percent", "ip"],
            expanded=True,
        )
        render_allocation_explain(data.get("allocation_explain", {}))
    with recovery_tab:
        render_cluster_table(
            "Shard recovery",
            data.get("recovery", []),
            ["index", "shard", "time", "type", "stage", "source_node", "target_node", "files_percent", "bytes_percent"],
            expanded=True,
        )


def render_cluster_resilience_demo() -> None:
    try:
        data = fetch_cluster_status()
    except requests.RequestException as exc:
        st.error(f"Backend is not ready: {request_error_detail(exc)}")
        return
    try:
        config = fetch_cluster_control_config()
    except requests.RequestException as exc:
        config = {"configured": False, "targets": []}
        st.warning(f"Cluster control config is not available: {request_error_detail(exc)}")

    render_cluster_status(data, config)


def run_search(experience_id: str, selected_query: str | None, limit: int, engine: str) -> None:
    try:
        params: dict[str, Any] = {"limit": limit}
        if selected_query:
            params["q"] = selected_query
        if experience_id == SEMANTIC_FEATURE_ID:
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
        elif experience_id == CLUSTER_FEATURE_ID:
            params["engine"] = "elasticsearch"
            data = get_json("/scenarios/scenario-1-product-search", params)
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
    div[data-testid="stVerticalBlock"] > div:has(iframe[title*="streamlit_autorefresh"]) {
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
    }
    iframe[title*="streamlit_autorefresh"] {
        display: none !important;
        height: 0 !important;
        min-height: 0 !important;
    }
    div[data-testid="stVerticalBlock"] > div:has(iframe[title*="streamlit_searchbox"]) {
        margin-top: -0.8rem;
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
    submitted = st.button("Search", use_container_width=True, key="search_button", help="Run search")

selected_suggestion = st_searchbox(
    suggestion_search,
    placeholder="Type product, review problem, or analytics keyword",
    label="Search query",
    key="query_box",
    clear_on_submit=False,
    edit_after_submit="current",
)

if selected_experience_id == CLUSTER_FEATURE_ID:
    render_cluster_resilience_demo()

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

if selected_experience_id == CLUSTER_FEATURE_ID:
    st_autorefresh(interval=CLUSTER_REFRESH_INTERVAL_MS, key="cluster_resilience_autorefresh")
