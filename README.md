# Amazon Electronics Search Demo

This demo compares three search technologies on a mini Amazon-like product
search use case:

- Elasticsearch
- Meilisearch
- PostgreSQL Full-Text Search
- Kibana for Elasticsearch inspection

The demo is organized into three comparative flows: advanced keyword search,
review evidence search, and search-driven review analytics. Semantic Search is
an Elasticsearch-only feature because it combines lexical search with vector
search inside Elasticsearch.

## Demo Features

- Full-text search across product metadata and review text
- Product discovery with Elasticsearch `multi_match`, field boosting, and fuzziness
- Elasticsearch search-as-you-type autocomplete over product titles
- Elasticsearch-only Semantic Search with Vertex AI embeddings, `dense_vector`, and KNN vector search
- Review evidence search with rating filters, helpful-vote tie-breaks, and highlights
- Filters by brand, category, price, and rating
- Faceted search and aggregations
- Keyword highlighting in search results
- Review analytics by brand, category, rating, and keyword
- Kibana Dev Tools checks for Elasticsearch cluster health, nodes, shards, and index stats
- Streamlit cluster resilience feature for 5-node, 4-node, and recovery demos

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
|-- scripts/
|   |-- download_datasets.py
|   |-- init_postgres.sql
|   |-- create_elasticsearch_indices.py
|   |-- create_meilisearch_indexes.py
|   |-- prepare_data.py
|   `-- ingest_all.py
`-- terraform/
    `-- gcp/
        |-- main.tf
        |-- terraform.tfvars.example
        `-- scripts/
            `-- startup.sh
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
start-demo
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

### Streamlit cluster resilience demo

Open Streamlit and select `Feature: Elasticsearch Cluster Resilience`.
The page shows the key cluster indicators: health, node count, online workers,
active shard percentage, unassigned shards, relocation, recovery activity,
nodes, shard placement, allocation, and recovery rows. It also includes a
single state-aware control button and an Elasticsearch product search test so
you can show search behavior while the cluster is healthy, degraded, and
recovering. The search test uses the same Input query and Search button as the
other scenarios/features. The cluster status auto-refreshes every 5 seconds.
When all 5 nodes are available, `Turn off random 1-2 workers` simulates
failure. In `Degraded mode`, `Turn on all offline workers` starts every
configured worker that is currently offline.

The GCP Terraform stack creates four private workers and installs an internal
master-to-worker SSH key at `/home/ubuntu/.ssh/id_ed25519_nexus_cluster` on the
master VM when `enable_master_worker_ssh = true`. The Docker Compose defaults
use that setup:

```env
ELASTICSEARCH_CONTROL_ENABLED=true
ELASTICSEARCH_CONTROL_TARGETS=nexus-worker-1=nexus-worker-1,nexus-worker-2=nexus-worker-2,nexus-worker-3=nexus-worker-3,nexus-worker-4=nexus-worker-4
ELASTICSEARCH_CONTROL_SSH_USER=ubuntu
ELASTICSEARCH_CONTROL_SSH_KEY_HOST=/home/ubuntu/.ssh/id_ed25519_nexus_cluster
ELASTICSEARCH_CONTROL_SSH_KEY=/run/secrets/es-control-ssh-key
ELASTICSEARCH_CONTROL_COMPOSE_DIR=/opt/nexus/docker-elk
ELASTICSEARCH_CONTROL_COMPOSE_ENV_FILES=.env,/etc/nexus-elastic.env
```

The backend service mounts `ELASTICSEARCH_CONTROL_SSH_KEY_HOST` into the
container automatically. The backend image includes `openssh-client`. Rebuild
the backend after changing the Dockerfile or control env:

```bash
docker compose --env-file .env --env-file /etc/nexus-elastic.env up -d --build backend frontend
```

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

All three engines ingest lexical/full-text fields for the comparative
scenarios. Elasticsearch also ingests `title_embedding` vectors for the
Elasticsearch-only Semantic Search feature. That feature requires Vertex AI
credentials during ingest unless you pass `--skip-embeddings`; if embeddings are
skipped, the Semantic Search feature will not have vectors to query.

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

The frontend has one search bar, a scenario/feature selector, a service
selector, and a result area split by engine. Comparative scenarios can run
against all three services. The Semantic Search feature is Elasticsearch-only
and forces the service to Elasticsearch.

| Scenario | Flow | User query | Demo goal | Main difference |
| --- | --- | --- | --- | --- |
| Scenario 1 | Product Search | `wireles noise canclling headphnes sony` | Find products by typo-heavy keywords across product metadata | Elasticsearch boosts fuzzy `multi_match` per field; Meilisearch leans on built-in typo tolerance; PostgreSQL FTS misses many typos without `pg_trgm` |
| Scenario 2 | Review Search | `battery dies after a week` | Surface review evidence that matches the user query | Elasticsearch combines text match, rating filter, helpful-vote sort, and highlighted snippets in one request |
| Scenario 3 | Analytics & Aggregation | `battery problem` | Find which brands/categories receive the most matching complaints and the rating distribution | Elasticsearch combines full-text search, filters, facets, and aggregations in one request |

Elasticsearch-only feature:

| Feature | Flow | User query | Demo goal | Main difference |
| --- | --- | --- | --- | --- |
| Semantic Search | Elasticsearch Semantic Search | `headphones for flights with quiet cabin noise` | Compare vector semantic retrieval with keyword retrieval in Elasticsearch | Left side runs KNN over the `title_embedding` `dense_vector` field with highlight disabled; right side runs keyword `multi_match` with highlight enabled |

Each comparative scenario shows 3 columns:

```text
Elasticsearch | Meilisearch | PostgreSQL FTS
```

The Semantic Search feature shows only the Elasticsearch result column.

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
matching reviews so Scenario 1 (product search) can search product metadata plus review language.
The Elasticsearch Semantic Search feature also stores `title_embedding`, a
768-dimensional `dense_vector` generated from product title, feature, and
description text. At query time the backend embeds the user query with Vertex AI
and compares two Elasticsearch retrieval modes: vector-only KNN search over
`title_embedding` with highlights disabled, and keyword `multi_match` with
highlights enabled.

## Main Endpoints

List scenarios/features and sample queries:

```bash
curl "http://localhost:8000/scenarios"
curl "http://localhost:8000/features"
```

Run a single scenario:

```bash
curl "http://localhost:8000/scenarios/scenario-1-product-search?q=wireles%20noise%20canclling%20headphnes%20sony"
curl "http://localhost:8000/scenarios/scenario-2-review-search?q=battery%20dies%20after%20a%20week"
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
curl "http://localhost:8000/features/elasticsearch/semantic-search?q=headphones%20for%20flights%20with%20quiet%20cabin%20noise"
```

`as-you-type` uses an Elasticsearch `search_as_you_type` field on product
titles. Semantic Search compares KNN vector search over the `title_embedding`
`dense_vector` field against lexical `multi_match`; re-run Elasticsearch ingest
with `--reset` after editing the vector mapping or regenerated embeddings.
