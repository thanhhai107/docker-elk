import random
import re
from typing import Any


VECTOR_DIMS = 384

DEMO_USERS = [
    {
        "user_id": "user_audiophile_minh",
        "display_name": "Audiophile Minh",
        "preferred_categories": ["Headphones"],
        "preferred_brands": ["Sony", "Bose", "Sennheiser", "Beats", "Jabra"],
        "price_min": 100,
        "price_max": 400,
    },
    {
        "user_id": "user_budget_nam",
        "display_name": "Budget Hunter Nam",
        "preferred_categories": ["Accessories", "Headphones"],
        "preferred_brands": ["Anker", "JLab", "AmazonBasics", "Mpow"],
        "price_min": 0,
        "price_max": 50,
    },
]

CONCEPTS = {
    "headphones": [
        "headphone",
        "headphones",
        "headset",
        "earbud",
        "earbuds",
        "earphone",
        "earphones",
        "tai nghe",
    ],
    "noise_cancel": [
        "anc",
        "noise cancelling",
        "noise canceling",
        "noise cancellation",
        "active noise cancellation",
        "chong on",
    ],
    "sport": [
        "working out",
        "workout",
        "sport",
        "sports",
        "gym",
        "running",
        "runner",
        "athletic",
        "active",
    ],
    "wireless": ["wireless", "bluetooth", "cordless", "true wireless"],
    "speaker": ["speaker", "speakers", "soundbar", "portable speaker"],
    "budget": ["cheap", "budget", "affordable", "under 50", "under 100", "duoi 500k"],
    "premium": ["premium", "audiophile", "hi-fi", "hifi", "studio", "flagship"],
}


def detect_concepts(value: str) -> list[str]:
    text = value.lower()
    return [
        concept
        for concept, phrases in CONCEPTS.items()
        if any(phrase in text for phrase in phrases)
    ]


def _parse_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_as_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_as_text(item) for item in value.values())
    return str(value)


def _stable_random(product_id: str) -> random.Random:
    seed = int.from_bytes(product_id.encode("utf-8")[:8].ljust(8, b"0"), "big")
    return random.Random(seed)


def normalize_amazon_product(raw: dict[str, Any]) -> dict[str, Any] | None:
    product_id = raw.get("parent_asin") or raw.get("asin") or raw.get("product_id")
    title = raw.get("title")
    if not product_id or not title:
        return None

    categories = raw.get("categories") or []
    category = raw.get("main_category") or (categories[-1] if categories else "Electronics")
    details = raw.get("details") or {}
    brand = raw.get("store") or details.get("Brand") or details.get("Manufacturer") or "Unknown"
    description = " ".join(
        item
        for item in [
            _as_text(raw.get("description")),
            _as_text(raw.get("features")),
        ]
        if item
    )
    price = _parse_price(raw.get("price"))
    rating = _parse_price(raw.get("average_rating")) or 0.0
    review_count = int(_parse_price(raw.get("rating_number")) or 0)
    rng = _stable_random(str(product_id))

    return {
        "product_id": str(product_id),
        "title": str(title),
        "description": description or str(title),
        "category": str(category),
        "brand": str(brand),
        "price": price if price is not None else round(rng.uniform(8, 350), 2),
        "rating": round(float(rating), 2),
        "review_count": review_count,
        "stock": rng.randint(0, 1000),
        "margin": round(rng.uniform(0.08, 0.8), 2),
    }


def demo_products() -> list[dict[str, Any]]:
    return [
        {
            "product_id": "demo-speaker-a-low",
            "title": "Portable Bluetooth Speaker Mini",
            "description": "Compact bluetooth speaker with basic sound for casual listening.",
            "category": "Speakers",
            "brand": "Generic",
            "price": 19.99,
            "rating": 2.1,
            "review_count": 5,
            "stock": 20,
            "margin": 0.12,
        },
        {
            "product_id": "demo-speaker-b-high",
            "title": "JBL Charge Premium Bluetooth Speaker",
            "description": "Waterproof bluetooth speaker with long battery life and rich bass.",
            "category": "Speakers",
            "brand": "JBL",
            "price": 129.99,
            "rating": 4.8,
            "review_count": 50000,
            "stock": 430,
            "margin": 0.42,
        },
        {
            "product_id": "demo-sony-wh1000xm4",
            "title": "Sony WH-1000XM4 Wireless Noise Cancelling Headphones",
            "description": "Premium over-ear headphones with active noise cancellation and balanced sound.",
            "category": "Headphones",
            "brand": "Sony",
            "price": 279.0,
            "rating": 4.8,
            "review_count": 52134,
            "stock": 15,
            "margin": 0.15,
        },
        {
            "product_id": "demo-bose-qc45",
            "title": "Bose QuietComfort 45 Wireless Headphones",
            "description": "Comfortable wireless headphones with active noise cancellation for travel.",
            "category": "Headphones",
            "brand": "Bose",
            "price": 329.0,
            "rating": 4.7,
            "review_count": 38920,
            "stock": 180,
            "margin": 0.31,
        },
        {
            "product_id": "demo-sennheiser-momentum",
            "title": "Sennheiser Momentum Wireless Headphones",
            "description": "Audiophile wireless headphones with detailed sound and premium materials.",
            "category": "Headphones",
            "brand": "Sennheiser",
            "price": 249.0,
            "rating": 4.6,
            "review_count": 18770,
            "stock": 90,
            "margin": 0.29,
        },
        {
            "product_id": "demo-business-margin",
            "title": "SoundMax Pro Wireless Noise Cancelling Headphones",
            "description": "Over-ear wireless headphones with active noise cancellation and strong battery life.",
            "category": "Headphones",
            "brand": "SoundMax",
            "price": 119.0,
            "rating": 4.6,
            "review_count": 21000,
            "stock": 850,
            "margin": 0.78,
        },
        {
            "product_id": "demo-anker-q20",
            "title": "Anker SoundCore Q20 Wireless Headphones",
            "description": "Affordable wireless headphones with hybrid active noise cancellation.",
            "category": "Headphones",
            "brand": "Anker",
            "price": 35.0,
            "rating": 4.4,
            "review_count": 27610,
            "stock": 640,
            "margin": 0.38,
        },
        {
            "product_id": "demo-jlab-studio",
            "title": "JLab Studio Wireless Headset",
            "description": "Budget wireless headset with noise control for everyday listening.",
            "category": "Headphones",
            "brand": "JLab",
            "price": 40.0,
            "rating": 4.3,
            "review_count": 14300,
            "stock": 520,
            "margin": 0.36,
        },
        {
            "product_id": "demo-mpow-h21",
            "title": "Mpow H21 Wireless Headphones",
            "description": "Low cost wireless headphones with comfortable ear cups.",
            "category": "Headphones",
            "brand": "Mpow",
            "price": 28.0,
            "rating": 4.2,
            "review_count": 11200,
            "stock": 470,
            "margin": 0.34,
        },
        {
            "product_id": "demo-jabra-active",
            "title": "Jabra Elite Active Sport Earbuds",
            "description": "Secure fit earbuds for gym, running, athletic training, and sweat resistance.",
            "category": "Headphones",
            "brand": "Jabra",
            "price": 129.0,
            "rating": 4.6,
            "review_count": 23300,
            "stock": 210,
            "margin": 0.33,
        },
        {
            "product_id": "demo-beats-powerbeats",
            "title": "Beats Powerbeats Pro Running Earphones",
            "description": "True wireless earphones with ear hooks for sport and gym use.",
            "category": "Headphones",
            "brand": "Beats",
            "price": 179.0,
            "rating": 4.5,
            "review_count": 31500,
            "stock": 160,
            "margin": 0.27,
        },
        {
            "product_id": "demo-sony-sport",
            "title": "Sony Sport Wireless Earbuds",
            "description": "Lightweight earbuds for running, gym sessions, and athletic training.",
            "category": "Headphones",
            "brand": "Sony",
            "price": 89.0,
            "rating": 4.5,
            "review_count": 17600,
            "stock": 290,
            "margin": 0.25,
        },
        {
            "product_id": "demo-usbc-cable",
            "title": "AmazonBasics USB-C Charging Cable",
            "description": "Durable accessory cable for phones, tablets, and laptops.",
            "category": "Accessories",
            "brand": "AmazonBasics",
            "price": 7.99,
            "rating": 4.6,
            "review_count": 81000,
            "stock": 1200,
            "margin": 0.51,
        },
        {
            "product_id": "demo-samsung-galaxy-s24",
            "title": "Samsung Galaxy S24 Smartphone",
            "description": "Android smartphone with AI camera, bright display, and fast performance.",
            "category": "Cell Phones",
            "brand": "Samsung",
            "price": 799.0,
            "rating": 4.7,
            "review_count": 48200,
            "stock": 370,
            "margin": 0.32,
        },
        {
            "product_id": "demo-samsung-galaxy-s24-ultra",
            "title": "Samsung Galaxy S24 Ultra Smartphone",
            "description": "Premium Galaxy phone with pro camera, S Pen, titanium design, and long battery life.",
            "category": "Cell Phones",
            "brand": "Samsung",
            "price": 1199.0,
            "rating": 4.8,
            "review_count": 35100,
            "stock": 240,
            "margin": 0.36,
        },
        {
            "product_id": "demo-iphone-15",
            "title": "Apple iPhone 15 Smartphone",
            "description": "iPhone with advanced camera, USB-C charging, and fast mobile performance.",
            "category": "Cell Phones",
            "brand": "Apple",
            "price": 799.0,
            "rating": 4.8,
            "review_count": 64300,
            "stock": 420,
            "margin": 0.34,
        },
        {
            "product_id": "demo-iphone-15-pro",
            "title": "Apple iPhone 15 Pro Smartphone",
            "description": "Premium iPhone with pro camera system, titanium body, and high-end performance.",
            "category": "Cell Phones",
            "brand": "Apple",
            "price": 999.0,
            "rating": 4.8,
            "review_count": 39100,
            "stock": 280,
            "margin": 0.37,
        },
    ]


def product_index_body() -> dict[str, Any]:
    return {
        "settings": {
            "number_of_shards": 4,
            "number_of_replicas": 1,
            "analysis": {
                "filter": {
                    "shopx_synonyms": {
                        "type": "synonym_graph",
                        "synonyms": [
                            "anc, active noise cancellation, noise cancellation, noise cancelling, noise canceling",
                            "headphone, headphones, headset, earbuds, earphones, tai nghe",
                            "working out, workout, gym, sport, sports, running, athletic",
                            "bluetooth, wireless, cordless",
                            "cheap, budget, affordable",
                            "chong on, noise cancelling, anc",
                        ],
                    }
                },
                "analyzer": {
                    "shopx_index_text": {
                        "tokenizer": "standard",
                        "filter": ["lowercase", "asciifolding"],
                    },
                    "shopx_search_text": {
                        "tokenizer": "standard",
                        "filter": ["lowercase", "asciifolding", "shopx_synonyms"],
                    },
                },
            },
        },
        "mappings": {
            "properties": {
                "product_id": {"type": "keyword"},
                "title": {
                    "type": "text",
                    "analyzer": "shopx_index_text",
                    "search_analyzer": "shopx_search_text",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                "title_suggest": {"type": "search_as_you_type"},
                "description": {
                    "type": "text",
                    "analyzer": "shopx_index_text",
                    "search_analyzer": "shopx_search_text",
                },
                "category": {
                    "type": "keyword",
                    "fields": {
                        "text": {
                            "type": "text",
                            "analyzer": "shopx_index_text",
                            "search_analyzer": "shopx_search_text",
                        }
                    },
                },
                "brand": {
                    "type": "keyword",
                    "fields": {
                        "text": {
                            "type": "text",
                            "analyzer": "shopx_index_text",
                            "search_analyzer": "shopx_search_text",
                        }
                    },
                },
                "price": {"type": "double"},
                "rating": {"type": "double"},
                "review_count": {"type": "integer"},
                "stock": {"type": "integer"},
                "margin": {"type": "double"},
                "semantic_terms": {"type": "keyword"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": VECTOR_DIMS,
                    "index": True,
                    "similarity": "cosine",
                },
            }
        },
    }


def simple_index_body() -> dict[str, Any]:
    return {
        "settings": {"number_of_shards": 1, "number_of_replicas": 1},
        "mappings": {
            "properties": {
                "query": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "user_id": {"type": "keyword"},
                "engine": {"type": "keyword"},
                "result_count": {"type": "integer"},
                "is_zero_result": {"type": "boolean"},
                "took_ms": {"type": "integer"},
                "timestamp": {"type": "date"},
            }
        },
    }


def users_index_body() -> dict[str, Any]:
    return {
        "settings": {"number_of_shards": 1, "number_of_replicas": 1},
        "mappings": {
            "properties": {
                "user_id": {"type": "keyword"},
                "display_name": {"type": "keyword"},
                "preferred_categories": {"type": "keyword"},
                "preferred_brands": {"type": "keyword"},
                "price_min": {"type": "double"},
                "price_max": {"type": "double"},
            }
        },
    }
