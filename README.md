# Amazon Electronics Search Demo

This demo compares three search technologies on a mini Amazon-like product
search use case:

- Elasticsearch
- Meilisearch
- PostgreSQL Full-Text Search

The demo is organized into three official flows: typo-heavy product discovery,
review evidence search, and review analytics/aggregation.

## Demo Features

- Full-text search across product metadata and review text
- Product discovery with Elasticsearch `multi_match`, field boosting, and fuzziness
- Review evidence search with rating filters, helpful-vote tie-breaks, and highlights
- Filters by brand, category, price, and rating
- Faceted search and aggregations
- Keyword highlighting in search results
- Review analytics by brand, category, rating, and keyword

## Cluster Layout

This project is intended to run on the GCP cluster. The same
`docker-compose.yml` runs a distributed Elasticsearch cluster using
`/etc/nexus-elastic.env` on each VM:

```text
nexus-master-1  -> Elasticsearch master/coordinating node
nexus-worker-1  -> Elasticsearch data/ingest node
nexus-worker-2  -> Elasticsearch data/ingest node
nexus-worker-3  -> Elasticsearch data/ingest node
nexus-worker-4  -> Elasticsearch data/ingest node
```

PostgreSQL, Meilisearch, the backend, and the frontend run on the master node.
Elasticsearch runs on the master and all worker nodes.

## Project Structure

```text
.
|-- docker-compose.yml
|-- data/
|   |-- download_datasets.py
|   |-- raw/
|   |-- sample/
|   |   |-- products.jsonl
|   |   `-- reviews.jsonl
|-- backend/
|   |-- main.py
|   |-- config.py
|   |-- ingest/
|   |-- services/
|   |-- models/
|   `-- utils/
|-- frontend/
|   `-- app.py
`-- scripts/
    |-- init_postgres.sql
    |-- create_elasticsearch_indices.py
    |-- create_meilisearch_indexes.py
    `-- ingest_all.py
```

## 1. Start the Cluster Services

### Start Elasticsearch on each worker

SSH into each worker node and run:

```bash
cd /opt/nexus/docker-elk
docker compose --env-file .env --env-file /etc/nexus-elastic.env up -d elasticsearch
```

If the helper script is available, you can run:

```bash
start-amazon-search-elasticsearch
```

### Start the master services

SSH into the master node and run:

```bash
cd /opt/nexus/docker-elk
docker compose --env-file .env --env-file /etc/nexus-elastic.env up -d --build postgres meilisearch elasticsearch backend frontend
```

If the helper script is available, you can run:

```bash
start-amazon-search-demo
```

### Check Elasticsearch cluster health

Run this on the master node:

```bash
curl "http://localhost:9200/_cat/nodes?v&h=name,node.role,master,ip"
curl "http://localhost:9200/_cluster/health?pretty"
```

You should see 5 Elasticsearch nodes. Product and review shards should be
distributed across the worker data nodes. The demo indices are created with
`number_of_shards=3` and `number_of_replicas=2`, so each shard has one primary
copy plus two replica copies on different Elasticsearch nodes.

## 2. Load Sample Data

Use this path when you want a quick demo with the committed sample files in
`data/sample`.

Run this on the master node:

```bash
cd /opt/nexus/docker-elk
docker compose exec -T backend python scripts/ingest_all.py --reset
```

This command resets and ingests up to 100,000 products. Matching reviews are
selected for those products with a per-product cap.

```text
Elasticsearch
Meilisearch
PostgreSQL Full-Text Search
```

Default ingest chunk sizes are balanced for the current demo workload:

```text
Elasticsearch bulk chunk: 1000 documents
Meilisearch document chunk: 2000 documents
PostgreSQL executemany chunk: 5000 rows
```

You can override them when needed:

```bash
docker compose exec -T backend python scripts/ingest_all.py --reset \
  --es-bulk-chunk-size 1000 \
  --meili-chunk-size 2000 \
  --postgres-chunk-size 5000
```

By default, review selection is balanced across products:

```text
--product-limit 100000
--max-reviews-per-product 5
```

This means the script accepts the first 100,000 valid products, then scans the
review file and accepts matching reviews for those products. No selected product
can contribute more than 5 reviews. Use `--max-reviews-per-product 0` to disable
the per-product cap.

## 3. Load Real Amazon Electronics Data

Use this path when you want to run the demo with the larger Amazon Electronics
dataset.

### Download the dataset

Run this on the master node:

```bash
cd /opt/nexus/docker-elk
docker compose exec -T backend python data/download_datasets.py
docker compose exec -T backend python data/download_datasets.py --reviews
```

The files are saved to:

```text
data/raw/meta_Electronics.jsonl.gz
data/raw/Electronics.jsonl.gz
```

### Ingest real data

```bash
docker compose exec -T backend python scripts/ingest_all.py --reset
```

The ingest script selects product data in file order, then balances review
selection across those products:

- `--product-limit 100000`: the first 100,000 valid products from
  `data/raw/meta_Electronics.jsonl.gz`.
- Reviews: all valid reviews from `data/raw/Electronics.jsonl.gz` whose
  `parent_asin` or `asin` matches one of the selected products, after applying
  `--max-reviews-per-product`.
- `--max-reviews-per-product 5`: no selected product contributes more than 5
  reviews by default. Use `0` to disable this cap.

## 4. Open the Demo

Frontend:

```text
http://localhost:8501
```

API docs:

```text
http://localhost:8000/docs
```

## Demo Scenarios

The frontend has one search bar, a scenario selector, a service selector, and a
result area split by engine.

| Scenario | Flow | User query | Demo goal | Main difference |
| --- | --- | --- | --- | --- |
| Scenario 1 | Product Discovery With Typos | `wireles noise canclling headphnes sony` | Find Sony wireless noise cancelling headphones despite multiple misspellings | Elasticsearch combines fuzzy search, field boosting, and ranking over `title`, `brand`, `features`, `description`, and `review_text` |
| Scenario 2 | Review Evidence Search | `battery dies after a week` | Return low-rating review snippets as evidence, prioritized by helpful votes | Elasticsearch combines review text search, `rating <= 2`, highlighting, and helpful-vote sorting |
| Scenario 3 | Review Analytics & Aggregation | `battery problem` | Find which brands/categories have the most negative battery-problem reviews and rating distribution | Elasticsearch combines full-text search, filters, facets, and aggregations in one request |

Each scenario shows 3 columns:

```text
Elasticsearch | Meilisearch | PostgreSQL FTS
```

Each column includes timing, request/query count, total hits, highlights,
aggregations/facets when available, and a short note about that engine.

## Amazon Data Field Mapping

The raw data follows the Amazon Reviews 2023 field layout.

Review source fields used by this demo:

| Raw field | Demo use |
| --- | --- |
| `rating` | Review rating filter and rating distribution. |
| `title` | Review title search and highlight. |
| `text` | Review evidence search, snippets, and analytics keyword match. |
| `asin` | Product ID fallback. |
| `parent_asin` | Primary product join key. |
| `user_id` | Reviewer ID retained on review documents. |
| `timestamp` | Review time retained for future filtering. |
| `verified_purchase` | Filterable review attribute in Meilisearch/PostgreSQL/Elasticsearch. |
| `helpful_vote` | Helpful-vote sort and aggregation signal. |

Item metadata source fields used by this demo:

| Raw field | Demo use |
| --- | --- |
| `parent_asin` | Normalized to `product_id`. Reviews join through `parent_asin` first, then `asin`. |
| `title` | Product title search, highlight, and product display. |
| `average_rating` | Product rating signal. |
| `rating_number` | Product popularity/review-count signal. |
| `features` | Product search field, boosted in Elasticsearch. |
| `description` | Product search field and display text. |
| `price` | Product filter/sort field. |
| `store` / `details.Brand` / `details.Manufacturer` | Normalized to `brand`. |
| `categories` / `main_category` | Normalized to `category`. |

The product index also stores a small aggregated `review_text` field from
matching reviews so Scenario 1 can search product metadata plus review language.

## Main Endpoints

List scenarios and sample queries:

```bash
curl "http://localhost:8000/scenarios"
```

Run a single scenario:

```bash
curl "http://localhost:8000/scenarios/scenario-1-product-discovery?q=wireles%20noise%20canclling%20headphnes%20sony"
curl "http://localhost:8000/scenarios/scenario-2-review-deep-search?q=battery%20dies%20after%20a%20week"
curl "http://localhost:8000/scenarios/scenario-3-review-analytics?q=battery%20problem"
```

Basic search endpoints:

```bash
curl "http://localhost:8000/search/elasticsearch?q=bluetooth%20speaker"
curl "http://localhost:8000/search/meilisearch?q=bluetooth%20speaker"
curl "http://localhost:8000/search/postgres?q=bluetooth%20speaker"
```
