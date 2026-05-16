# Amazon Electronics Search Demo

This demo compares three search technologies on a mini Amazon-like product
search use case:

- Elasticsearch
- Meilisearch
- PostgreSQL Full-Text Search
- Kibana for Elasticsearch inspection

The demo is organized into three official flows: advanced keyword search,
native semantic search, and search-driven review analytics.

## Demo Features

- Full-text search across product metadata and review text
- Product discovery with Elasticsearch `multi_match`, field boosting, and fuzziness
- Elasticsearch search-as-you-type autocomplete over product titles
- Elasticsearch native semantic search with `semantic_text` and Elastic inference
- Review evidence search with rating filters, helpful-vote tie-breaks, and highlights
- Filters by brand, category, price, and rating
- Faceted search and aggregations
- Keyword highlighting in search results
- Review analytics by brand, category, rating, and keyword
- Kibana Dev Tools checks for Elasticsearch cluster health, nodes, shards, and index stats

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
    |-- download_datasets.py
    |-- init_postgres.sql
    |-- create_elasticsearch_indices.py
    |-- create_meilisearch_indexes.py
    |-- prepare_data.py
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
docker compose --env-file .env --env-file /etc/nexus-elastic.env up -d --build postgres meilisearch elasticsearch kibana backend frontend
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

Loading and ingest are split into two steps. Step 2a reads the source files,
normalizes/enriches records, and writes processed JSONL plus a manifest under
`data/processed/`. Step 2b reads those processed files and pushes them into
each engine. You can re-run step 2b without redoing the slow load step.

Step 2a (load + enrich):

```bash
cd /opt/nexus/docker-elk
docker compose exec -T backend python scripts/prepare_data.py
```

Step 2b (ingest into engines):

```bash
docker compose exec -T backend python scripts/ingest_all.py --reset
```

`prepare_data.py` accepts up to 100,000 products by default. Matching reviews
are selected for those products with a per-product cap.

```text
Elasticsearch
Meilisearch
PostgreSQL Full-Text Search
```

Default ingest chunk sizes are balanced for the current demo workload:

```text
Elasticsearch bulk chunk: 500 documents
Meilisearch document chunk: 2000 documents
PostgreSQL executemany chunk: 5000 rows
```

You can override them when needed:

```bash
docker compose exec -T backend python scripts/ingest_all.py --reset \
  --es-bulk-chunk-size 500 \
  --es-request-timeout 600 \
  --es-max-retries 5 \
  --meili-chunk-size 2000 \
  --postgres-chunk-size 5000
```

Elasticsearch uses `semantic_text` for Scenario 2, so product ingest may call
Elastic inference. Meilisearch and PostgreSQL ingest only lexical/full-text
fields for that scenario.

By default, `prepare_data.py` balances review selection across products:

```text
--product-limit 100000
--max-reviews-per-product 5
```

This means `prepare_data.py` accepts the first 100,000 valid products, then
scans the review file and accepts matching reviews for those products. No
selected product can contribute more than 5 reviews. Use
`--max-reviews-per-product 0` to disable the per-product cap.

You can re-run a single engine without redoing the load step. For example,
after fixing an Elasticsearch mapping issue:

```bash
docker compose exec -T backend python scripts/ingest_all.py --reset --engine elasticsearch
```

Pass `--processed-dir <path>` to either script if you want a non-default
location.

## 3. Load Real Amazon Electronics Data

Use this path when you want to run the demo with the larger Amazon Electronics
dataset.

### Download the dataset

Run this on the master node:

```bash
cd /opt/nexus/docker-elk
docker compose exec -T backend python scripts/download_datasets.py
docker compose exec -T backend python scripts/download_datasets.py --reviews
```

The files are saved to:

```text
data/raw/meta_Electronics.jsonl.gz
data/raw/Electronics.jsonl.gz
```

### Prepare and ingest real data

```bash
docker compose exec -T backend python scripts/prepare_data.py \
  --product-limit 100000 \
  --max-reviews-per-product 5

docker compose exec -T backend python scripts/ingest_all.py --reset
```

`prepare_data.py` selects product data in file order, then balances review
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

Kibana:

```text
http://localhost:5601
```

Use Kibana Dev Tools to inspect Elasticsearch scale and shard distribution:

```http
GET _cluster/health?pretty
GET _cat/nodes?v&h=name,node.role,master,ip,heap.percent,ram.percent,cpu,load_1m
GET _cat/shards/amazon_electronics_products?v
GET _cat/shards/amazon_electronics_reviews?v
GET amazon_electronics_products/_stats
GET _cat/thread_pool/search?v
```

## Demo Scenarios

The frontend has one search bar, a scenario selector, a service selector, and a
result area split by engine.

| Scenario | Flow | User query | Demo goal | Main difference |
| --- | --- | --- | --- | --- |
| Scenario 1 | Full-text/Keyword Search | Product: `wireles noise canclling headphnes sony`; review evidence: `battery dies after a week` | Show keyword search for both typo-heavy product discovery and highlighted review evidence | Elasticsearch combines fuzzy boosted product search with review `rating <= 2`, highlighting, and helpful-vote sorting |
| Scenario 2 | Semantic Search | `headphones for flights and office calls` | Show what remains when external models and app-generated embeddings are not allowed | Elasticsearch uses `semantic_text` with Elastic inference; Meilisearch and PostgreSQL fall back to full-text search |
| Scenario 3 | Analytics & Aggregation | `battery problem` | Find which brands/categories have the most negative battery-problem reviews and rating distribution | Elasticsearch combines full-text search, filters, facets, and aggregations in one request |

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
For Scenario 2, Elasticsearch stores combined product text in a `semantic_text`
field. Meilisearch and PostgreSQL do not receive app-generated embeddings in
this no-external-model setup.

Set `ELASTIC_SEMANTIC_INFERENCE_ID` before creating indices if you want to bind
the `semantic_text` field to a specific Elastic inference endpoint. If it is not
set, Elasticsearch uses the default semantic inference endpoint configured for
the cluster.

### Deploy ELSER for semantic search

`scripts/setup_elser.sh` deploys ELSER v2 onto the cluster as the inference
endpoint named `my-elser` and writes `ELASTIC_SEMANTIC_INFERENCE_ID=my-elser`
into `/opt/nexus/docker-elk/.env`. The script is idempotent.

Prerequisite: at least one Elasticsearch node must have the `ml` role. The
Terraform startup script assigns `data,ingest,ml` to `nexus-worker-1` by
default.

Run on the master VM:

```bash
cd /opt/nexus/docker-elk
bash scripts/setup_elser.sh

# Re-create backend so it picks up the new env var
docker compose --env-file .env --env-file /etc/nexus-elastic.env \
  up -d --force-recreate backend

# Re-ingest only Elasticsearch (processed JSONL is reused)
docker compose exec -T backend python scripts/ingest_all.py --reset --engine elasticsearch
```

Tunables via env vars: `INFERENCE_ID`, `MODEL_ID`, `MIN_ALLOCATIONS`,
`MAX_ALLOCATIONS`, `NUM_THREADS`, `WAIT_TIMEOUT_SECONDS`, `ES_URL`,
`ENV_FILE`. Use `MODEL_ID=.elser_model_2` for non-x86_64 hosts.

## Main Endpoints

List scenarios and sample queries:

```bash
curl "http://localhost:8000/scenarios"
```

Run a single scenario:

```bash
curl "http://localhost:8000/scenarios/scenario-1-full-text-keyword-search?q=wireles%20noise%20canclling%20headphnes%20sony"
curl "http://localhost:8000/scenarios/scenario-2-semantic-search?q=headphones%20for%20flights%20and%20office%20calls"
curl "http://localhost:8000/scenarios/scenario-3-analytics-aggregation?q=battery%20problem"
```

Basic search endpoints:

```bash
curl "http://localhost:8000/search/elasticsearch?q=bluetooth%20speaker"
curl "http://localhost:8000/search/meilisearch?q=bluetooth%20speaker"
curl "http://localhost:8000/search/postgres?q=bluetooth%20speaker"
```

Elasticsearch-specific capabilities:

```bash
curl "http://localhost:8000/search/elasticsearch/as-you-type?q=sony%20wh"
curl "http://localhost:8000/search/elasticsearch/semantic?q=headphones%20for%20flights%20with%20quiet%20cabin%20noise"
```

`as-you-type` uses an Elasticsearch `search_as_you_type` field on product
titles. Semantic search uses Elasticsearch `semantic_text`; re-run ingest with
`--reset` after mapping changes.
