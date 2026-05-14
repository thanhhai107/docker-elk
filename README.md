# Amazon Electronics Search Demo

This demo compares three search technologies on a mini Amazon-like product
search use case:

- Elasticsearch
- Meilisearch
- PostgreSQL Full-Text Search

The scenarios focus on five official product-search scenarios: imperfect product discovery,
deep review search, review analytics/aggregation, natural-language product
recommendations, and worker-failover resilience.

## Demo Features

- Full-text search across product metadata and review text
- Product discovery with Elasticsearch `multi_match`, field boosting, and fuzziness
- Review deep search with rating filters, helpful-vote tie-breaks, and highlights
- Filters by brand, category, price, and rating
- Faceted search and aggregations
- Keyword highlighting in search results
- Review analytics by brand, category, rating, and keyword
- Hybrid-style recommendation search using natural-language intent expansion
- Worker-failover check with batch query latency, top-10 retrieval, cluster health, and shard/replica state
- Benchmarking by total workflow time, not just raw search time

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

This command resets and ingests up to 2,000,000 products and 200,000 reviews
from the selected data files into:

```text
Elasticsearch
Meilisearch
PostgreSQL Full-Text Search
```

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

The ingest script selects data in file order:

- `--product-limit 2000000`: the first 2,000,000 valid products from
  `data/raw/meta_Electronics.jsonl.gz`.
- `--review-limit 200000`: the first 200,000 valid reviews from
  `data/raw/Electronics.jsonl.gz` whose `parent_asin` or `asin` matches one of
  the selected products.

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

The frontend has 6 tabs:

1. ACT 1: Product Discovery Search
2. ACT 2: Review Deep Search
3. ACT 3: Review Analytics & Aggregation
4. ACT 4: Hybrid / Semantic Recommendation
5. ACT 5: Worker Failover / Scale Resilience
6. Benchmark Report

Each scenario shows 3 columns:

```text
Elasticsearch | Meilisearch | PostgreSQL FTS
```

Each column includes timing, request/query count, total hits, highlights,
aggregations/facets when available, and a short note about that engine.

## Main Endpoints

List scenarios and sample queries:

```bash
curl "http://localhost:8000/scenarios"
```

Run a single scenario:

```bash
curl "http://localhost:8000/scenarios/act-1-product-discovery?q=iphne%20charger%20fast%20charging"
curl "http://localhost:8000/scenarios/act-2-review-deep-search?q=battery%20drains%20fast"
curl "http://localhost:8000/scenarios/act-3-review-analytics"
curl "http://localhost:8000/scenarios/act-4-hybrid-recommendation?q=I%20need%20headphones%20for%20online%20meetings%20with%20good%20battery%20and%20noise%20cancellation"
curl "http://localhost:8000/scenarios/act-5-scale-readiness"
```

## ACT 5 Worker Failover Test

ACT 5 is meant to be run multiple times while changing the Elasticsearch worker
set:

```bash
curl "http://localhost:8000/scenarios/act-5-scale-readiness"
```

Then stop one Elasticsearch worker node and run it again. Stop a second worker
node and run it again.

Pass condition:

```text
- product/review queries still return top-10 results
- Elasticsearch cluster status is green or yellow
- active_primary_shards is still greater than 0
- configured_replicas is at least 2 for product and review indices
- no search errors are returned
```

Fail condition:

```text
- cluster status is red
- primary shards are unavailable
- number_of_replicas is less than 2 for the demo indices
- searches fail or return engine errors
```

If the indices already existed before this setting was added, run ingest with
`--reset` so the indices are recreated with two replicas:

```bash
docker compose exec -T backend python scripts/ingest_all.py --reset
```

Meilisearch and PostgreSQL in this compose stack run as single services, so they
do not provide the same worker-failover behavior unless external HA/replication
is added.

Benchmark all workflows:

```bash
curl "http://localhost:8000/workflow-benchmark"
```

Basic search endpoints:

```bash
curl "http://localhost:8000/compare?q=bluetooth%20speaker"
curl "http://localhost:8000/search/elasticsearch?q=bluetooth%20speaker"
curl "http://localhost:8000/search/meilisearch?q=bluetooth%20speaker"
curl "http://localhost:8000/search/postgres?q=bluetooth%20speaker"
```
