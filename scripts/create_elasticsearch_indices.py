from __future__ import annotations

from elasticsearch import Elasticsearch

from backend.config import settings


PRODUCT_MAPPING = {
    "settings": {
        "number_of_shards": 3,
        "number_of_replicas": 2,
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
            "features": {"type": "text", "analyzer": "product_index", "search_analyzer": "product_search"},
            "description": {"type": "text", "analyzer": "product_index", "search_analyzer": "product_search"},
            "review_text": {"type": "text", "analyzer": "product_index", "search_analyzer": "product_search"},
            "category": {"type": "keyword", "fields": {"text": {"type": "text"}}},
            "brand": {"type": "keyword", "fields": {"text": {"type": "text"}}},
            "price": {"type": "double"},
            "rating": {"type": "double"},
            "review_count": {"type": "integer"},
            "average_rating": {"type": "double"},
            "rating_number": {"type": "integer"},
            "avg_review_rating": {"type": "double"},
            "loaded_review_count": {"type": "integer"},
            "helpful_votes": {"type": "integer"},
        }
    },
}

REVIEW_MAPPING = {
    "settings": {
        "number_of_shards": 3,
        "number_of_replicas": 2,
        "analysis": {
            "analyzer": {
                "review_text": {"tokenizer": "standard", "filter": ["lowercase", "asciifolding"]}
            }
        }
    },
    "mappings": {
        "properties": {
            "review_id": {"type": "keyword"},
            "product_id": {"type": "keyword"},
            "user_id": {"type": "keyword"},
            "brand": {"type": "keyword"},
            "category": {"type": "keyword"},
            "rating": {"type": "double"},
            "title": {"type": "text", "analyzer": "review_text"},
            "text": {"type": "text", "analyzer": "review_text"},
            "helpful_vote": {"type": "integer"},
            "verified_purchase": {"type": "boolean"},
            "timestamp": {"type": "date", "format": "epoch_millis||epoch_second||strict_date_optional_time"},
        }
    },
}


def create_indices(reset: bool = False) -> None:
    client = Elasticsearch(settings.elasticsearch_url, request_timeout=60)
    indices = {
        "amazon_electronics_products": PRODUCT_MAPPING,
        "amazon_electronics_reviews": REVIEW_MAPPING,
    }
    for index, mapping in indices.items():
        if reset and client.indices.exists(index=index):
            client.indices.delete(index=index)
        if not client.indices.exists(index=index):
            client.indices.create(index=index, body=mapping)


if __name__ == "__main__":
    create_indices(reset=True)
