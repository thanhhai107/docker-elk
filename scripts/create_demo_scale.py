import argparse
from datetime import UTC, datetime

from elasticsearch import Elasticsearch, helpers


def docs(count: int):
    categories = ["headphones", "speaker", "phone", "accessory"]
    brands = ["Sony", "Samsung", "Apple", "Anker", "JBL", "Bose"]
    for idx in range(count):
        yield {
            "_index": "demo_scale",
            "_id": f"scale-{idx}",
            "_source": {
                "doc_id": f"scale-{idx}",
                "title": f"Scale demo product {idx}",
                "category": categories[idx % len(categories)],
                "brand": brands[idx % len(brands)],
                "price": round(10 + (idx % 500) * 1.7, 2),
                "created_at": datetime.now(UTC).isoformat(),
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create demo_scale with 2 primary shards, 1 replica, and sample documents."
    )
    parser.add_argument("--es-url", default="http://127.0.0.1:9200")
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    es = Elasticsearch(args.es_url, request_timeout=60)
    if args.reset and es.indices.exists(index="demo_scale"):
        es.indices.delete(index="demo_scale")

    if not es.indices.exists(index="demo_scale"):
        es.indices.create(
            index="demo_scale",
            settings={
                "number_of_shards": 2,
                "number_of_replicas": 1,
            },
            mappings={
                "properties": {
                    "doc_id": {"type": "keyword"},
                    "title": {"type": "text"},
                    "category": {"type": "keyword"},
                    "brand": {"type": "keyword"},
                    "price": {"type": "double"},
                    "created_at": {"type": "date"},
                }
            },
        )

    helpers.bulk(es, docs(args.count), chunk_size=1_000, request_timeout=120)
    es.indices.refresh(index="demo_scale")
    print(es.count(index="demo_scale"))
    print(es.cluster.health(index="demo_scale"))


if __name__ == "__main__":
    main()
