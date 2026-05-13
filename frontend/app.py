from __future__ import annotations

import os
from html import unescape

import requests
import streamlit as st


BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")


def get_json(path: str, params: dict | None = None) -> dict:
    response = requests.get(f"{BACKEND_URL}{path}", params=params, timeout=60)
    response.raise_for_status()
    return response.json()


def clean_highlight(value: str) -> str:
    return unescape(value or "").replace("<em>", "<mark>").replace("</em>", "</mark>")


st.set_page_config(page_title="Amazon Search Demo", layout="wide")
st.title("Amazon Electronics Search Comparison")

with st.sidebar:
    st.header("Search")
    query = st.text_input("Query", "wireless noise cancelling headphones")
    brand = st.text_input("Brand")
    category = st.text_input("Category")
    min_price, max_price = st.slider("Price range", 0, 1500, (0, 500), step=10)
    min_rating = st.slider("Minimum rating", 0.0, 5.0, 4.0, step=0.1)
    limit = st.slider("Results per engine", 3, 20, 5)
    run = st.button("Compare", type="primary")

params = {
    "q": query,
    "limit": limit,
    "min_price": min_price,
    "max_price": max_price,
    "min_rating": min_rating,
}
if brand:
    params["brand"] = brand
if category:
    params["category"] = category

if run or query:
    try:
        comparison = get_json("/compare", params)
        analytics = get_json("/analytics/reviews")
    except requests.RequestException as exc:
        st.error(f"Backend is not ready: {exc}")
        st.stop()

    metric_cols = st.columns(3)
    for column, result in zip(metric_cols, comparison["results"]):
        column.metric(
            result["engine"],
            "error" if result.get("error") else f'{result.get("took_ms", 0)} ms',
            f'{result.get("total", 0)} hits',
        )

    st.subheader("Review Analytics")
    st.json(analytics, expanded=False)

    tabs = st.tabs([result["engine"] for result in comparison["results"]])
    for tab, result in zip(tabs, comparison["results"]):
        with tab:
            if result.get("error"):
                st.error(result["error"])
                continue

            left, right = st.columns([3, 1])
            with right:
                st.caption("Facets")
                st.write("Brands")
                st.dataframe(result.get("facets", {}).get("brands", []), use_container_width=True)
                st.write("Categories")
                st.dataframe(result.get("facets", {}).get("categories", []), use_container_width=True)

            with left:
                for index, hit in enumerate(result.get("hits", []), start=1):
                    highlights = hit.get("highlights", {})
                    title = clean_highlight((highlights.get("title") or [hit.get("title", "")])[0])
                    description = clean_highlight(
                        (highlights.get("description") or [hit.get("description", "")])[0]
                    )
                    st.markdown(f"#### {index}. {title}", unsafe_allow_html=True)
                    st.caption(
                        f'{hit.get("brand", "Unknown")} | {hit.get("category", "Electronics")} | '
                        f'${hit.get("price", 0):,.2f} | rating {hit.get("rating", 0)} | '
                        f'score {hit.get("score")}'
                    )
                    st.markdown(description, unsafe_allow_html=True)
                    st.caption(
                        f'Loaded reviews: {hit.get("loaded_review_count", 0)} | '
                        f'Avg review rating: {hit.get("avg_review_rating", 0)} | '
                        f'Helpful votes: {hit.get("helpful_votes", 0)}'
                    )
                    st.divider()
