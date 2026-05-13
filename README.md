# Amazon Electronics Search Demo

This demo compares three search technologies on a mini Amazon-like product
search use case:

- Elasticsearch
- Meilisearch
- PostgreSQL Full-Text Search

The scenarios focus on realistic end-to-end workflows: business-aware ranking,
complex filtering, highlighting, faceted search, aggregations, review analytics,
and workflow benchmarking.

## Demo Features

- Full-text search across product metadata and review text
- Advanced ranking using relevance + rating + review volume
- Filters by brand, category, price, and rating
- Faceted search and aggregations
- Keyword highlighting in search results
- Negative review analytics
- Complex query intent with `must`, `should`, `filter`, and `must_not`
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
distributed across the worker data nodes.

## 2. Load Sample Data

Use this path when you want a quick demo with the committed sample files in
`data/sample`.

Run this on the master node:

```bash
cd /opt/nexus/docker-elk
docker compose exec -T backend python scripts/ingest_all.py --reset
```

This command resets and ingests the sample products and reviews into:

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
docker compose exec -T backend python scripts/ingest_all.py --reset --product-limit 50000 --review-limit 50000
```

The ingest script selects data in file order:

- `--product-limit 50000`: the first 50,000 valid products from
  `data/raw/meta_Electronics.jsonl.gz`.
- `--review-limit 50000`: the first 50,000 valid reviews from
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

1. Advanced Ranking
2. Search + Filter + Facet
3. Negative Review Analytics
4. Complex Query Intent
5. Admin Dashboard Insights
6. Workflow Benchmark

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
curl "http://localhost:8000/scenarios/advanced-ranking"
curl "http://localhost:8000/scenarios/search-filter-facet"
curl "http://localhost:8000/scenarios/negative-review-analytics"
curl "http://localhost:8000/scenarios/complex-query-intent"
curl "http://localhost:8000/scenarios/admin-dashboard-insights"
```

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
