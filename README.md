# Amazon Electronics Search Demo

Demo nay so sanh 3 cong nghe tim kiem tren bai toan product search giong
Amazon mini:

- Elasticsearch
- Meilisearch
- PostgreSQL Full-Text Search

Trong demo nay, Elasticsearch khong duoc cho thang bang keyword search don
gian. Cac scenario tap trung vao end-to-end workflow thuc te: ranking theo
business logic, filter phuc tap, highlight, faceted search, aggregation va
review analytics.

## Chuc Nang Demo

- Full-text search tren product metadata va review text
- Advanced ranking bang relevance + rating + review volume
- Filter theo brand, category, price, rating
- Faceted search va aggregation
- Highlight tu khoa trong ket qua
- Negative review analytics
- Complex query intent voi must, should, filter, must_not
- Benchmark theo total workflow time, khong chi search time

## Cau Truc

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

## Chay Nhanh Bang Sample Data

Neu da tung chay version cu, reset volume truoc:

```sh
docker compose down -v
```

Sau do:

```sh
cp .env.example .env
docker compose up -d --build
docker compose exec backend python scripts/ingest_all.py --reset
```

Mo giao dien:

```text
http://localhost:8501
```

API docs:

```text
http://localhost:8000/docs
```

## Chay Elasticsearch Phan Tan Tren GCP Cluster

Local Docker Compose co the chay 1 Elasticsearch node de dev nhanh. Tren cum
GCP, cung mot `docker-compose.yml` se chay Elasticsearch phan tan bang
`/etc/nexus-elastic.env` tren tung VM:

```text
nexus-master-1  -> Elasticsearch master/coordinating node
nexus-worker-1  -> Elasticsearch data/ingest node
nexus-worker-2  -> Elasticsearch data/ingest node
nexus-worker-3  -> Elasticsearch data/ingest node
nexus-worker-4  -> Elasticsearch data/ingest node
```

Thu tu khoi dong khuyen nghi:

1. SSH vao tung worker va chay:

```bash
cd /opt/nexus/docker-elk
docker compose --env-file .env --env-file /etc/nexus-elastic.env up -d elasticsearch
```

2. SSH vao master va chay:

```bash
cd /opt/nexus/docker-elk
docker compose --env-file .env --env-file /etc/nexus-elastic.env up -d --build postgres meilisearch elasticsearch backend frontend
docker compose exec -T backend python scripts/ingest_all.py --reset
```

Neu startup script moi da duoc ap dung, co the dung helper:

```bash
start-amazon-search-elasticsearch   # tren tung worker
start-amazon-search-demo            # tren master
```

Kiem tra cluster:

```bash
curl "http://localhost:9200/_cat/nodes?v&h=name,node.role,master,ip"
curl "http://localhost:9200/_cluster/health?pretty"
```

Ban nen thay 5 node Elasticsearch. Cac shard product/review se duoc phan bo
tren worker data nodes.

## Dung Amazon Electronics Dataset

Tai metadata san pham:

```sh
python data/download_datasets.py
```

Tai them review events:

```sh
python data/download_datasets.py --reviews
```

Script se luu file vao:

```text
data/raw/meta_Electronics.jsonl.gz
data/raw/Electronics.jsonl.gz
```

Ingest vao ca 3 engine:

```sh
docker compose exec backend python scripts/ingest_all.py --reset --product-limit 5000 --review-limit 20000
```

Tang limit neu may du RAM va thoi gian ingest:

```sh
docker compose exec backend python scripts/ingest_all.py --reset --product-limit 80000 --review-limit 200000
```

## Demo Scenarios

Frontend co 6 tab:

1. Advanced Ranking
2. Search + Filter + Facet
3. Negative Review Analytics
4. Complex Query Intent
5. Admin Dashboard Insights
6. Workflow Benchmark

Moi scenario hien thi 3 cot:

```text
Elasticsearch | Meilisearch | PostgreSQL FTS
```

Moi cot co time, so request/query, total hits, highlights, aggregations/facets
neu co, va nhan xet ngan ve engine do.

## Endpoint Chinh

Danh sach scenario va query mau:

```sh
curl "http://localhost:8000/scenarios"
```

Chay mot scenario:

```sh
curl "http://localhost:8000/scenarios/advanced-ranking"
curl "http://localhost:8000/scenarios/search-filter-facet"
curl "http://localhost:8000/scenarios/negative-review-analytics"
curl "http://localhost:8000/scenarios/complex-query-intent"
curl "http://localhost:8000/scenarios/admin-dashboard-insights"
```

Benchmark tat ca workflow:

```sh
curl "http://localhost:8000/workflow-benchmark"
```

Endpoint search co ban van duoc giu lai:

```sh
curl "http://localhost:8000/compare?q=bluetooth%20speaker"
curl "http://localhost:8000/search/elasticsearch?q=bluetooth%20speaker"
curl "http://localhost:8000/search/meilisearch?q=bluetooth%20speaker"
curl "http://localhost:8000/search/postgres?q=bluetooth%20speaker"
```

## Ket Luan Demo

Elasticsearch:

- Manh nhat khi workflow can search + ranking + filter + highlight + aggregation trong cung mot request.
- `function_score`, `bool query`, `must/should/filter/must_not`, `terms`, `range`, `stats`, highlight deu nam trong DSL.
- Phu hop lam search engine va analytics engine cho e-commerce lon.

Meilisearch:

- Rat tot cho search UI don gian, nhanh, typo tolerance tot.
- Co facet/filter, nhung analytics metric va custom ranking phuc tap khong linh hoat bang Elasticsearch.

PostgreSQL Full-Text Search:

- Phu hop khi du lieu da nam trong relational database va bai toan search vua phai.
- Lam duoc nhieu workflow, nhung SQL dai hon, can nhieu query hon, backend phai merge ket qua.

Ket luan can trinh bay:

```text
Qua cac scenario thuc te voi Amazon Electronics Dataset, Elasticsearch outperform Meilisearch va PostgreSQL Full-Text Search o cac workflow phuc tap.

Meilisearch rat phu hop cho search UI don gian, toc do nhanh va typo tolerance tot.

PostgreSQL Full-Text Search phu hop neu du lieu da nam trong database va bai toan search khong qua phuc tap.

Tuy nhien, khi he thong can search nhieu field, ranking theo business logic, filter phuc tap, highlight, faceted search, aggregation va review analytics, Elasticsearch la lua chon manh hon.
```

## Ghi Chu

Raw dataset lon khong duoc commit. Repo chi commit `data/sample` de demo nhanh.
Neu chay tren VM thay vi may local, nen dung SSH tunnel cho PostgreSQL,
Elasticsearch va Meilisearch thay vi mo public cac port engine.
