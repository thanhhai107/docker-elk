from __future__ import annotations

from elasticsearch import Elasticsearch

from backend.config import settings


PRODUCT_MAPPING = {
    "settings": {
        "analysis": {
            "filter": {
                "product_synonyms": {
                    "type": "synonym_graph",
                    "synonyms": [
                        "anc, active noise cancellation, noise cancelling, noise canceling",
                        "headphone, headphones, headset, earbuds, earphones",
                        "bluetooth, wireless, cordless",
                        "cheap, budget, affordable",
                    ],
                }
            },
            "analyzer": {
                "product_index": {"tokenizer": "standard", "filter": ["lowercase", "asciifolding"]},
                "product_search": {
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding", "product_synonyms"],
                },
            },
        }
    },
    "mappings": {
        "properties": {
            "product_id": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "product_index",
                "search_analyzer": "product_search",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "description": {"type": "text", "analyzer": "product_index", "search_analyzer": "product_search"},
            "review_text": {"type": "text", "analyzer": "product_index", "search_analyzer": "product_search"},
            "category": {"type": "keyword", "fields": {"text": {"type": "text"}}},
            "brand": {"type": "keyword", "fields": {"text": {"type": "text"}}},
            "price": {"type": "double"},
            "rating": {"type": "double"},
            "review_count": {"type": "integer"},
            "avg_review_rating": {"type": "double"},
            "loaded_review_count": {"type": "integer"},
            "helpful_votes": {"type": "integer"},
        }
    },
}


def create_indices(reset: bool = False) -> None:
    client = Elasticsearch(settings.elasticsearch_url, request_timeout=60)
    if reset and client.indices.exists(index="products"):
        client.indices.delete(index="products")
    if not client.indices.exists(index="products"):
        client.indices.create(index="products", body=PRODUCT_MAPPING)


if __name__ == "__main__":
    create_indices(reset=True)
