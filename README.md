# Amazon Electronics Search Demo

Demo nay so sanh 3 cong nghe tim kiem tren bai toan product search giong
Amazon mini:

- Elasticsearch
- Meilisearch
- PostgreSQL Full-Text Search

Ung dung dung FastAPI cho backend, Streamlit cho giao dien demo, Docker Compose
cho Elasticsearch, Meilisearch va PostgreSQL. Dataset dau vao la Amazon
Electronics metadata va review JSONL/GZ.

## Chuc Nang Demo

- Full-text search tren title, description va review text
- Typo tolerant / fuzzy search
- Filter theo brand, category, price, rating
- Faceted search / aggregation theo brand va category
- Highlight tu khoa trong ket qua
- Review analytics tu review events
- So sanh latency cua Elasticsearch, Meilisearch va PostgreSQL
- So sanh do phu hop bang danh sach ket qua cua tung engine

## Cau Truc

```text
.
├── docker-compose.yml
├── data/
│   ├── download_datasets.py
│   ├── products.jsonl              # optional local input, ignored by Git
│   ├── reviews.jsonl               # optional local input, ignored by Git
│   ├── raw/                        # downloaded Amazon files, ignored by Git
│   └── sample/                     # small demo dataset
├── backend/
│   ├── main.py
│   ├── config.py
│   ├── ingest/
│   ├── services/
│   ├── models/
│   └── utils/
├── frontend/
│   └── app.py
└── scripts/
    ├── init_postgres.sql
    ├── create_elasticsearch_indices.py
    ├── create_meilisearch_indexes.py
    └── ingest_all.py
```

## Chay Nhanh Bang Sample Data

```sh
cp .env.example .env
docker compose up -d --build
docker compose exec backend python scripts/ingest_all.py --reset
```

Neu truoc do da chay repo voi PostgreSQL credentials cu, reset volume truoc:

```sh
docker compose down -v
```

Mo giao dien:

```text
http://localhost:8501
```

API backend:

```text
http://localhost:8000/docs
```

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

## Endpoint Chinh

So sanh 3 engine:

```sh
curl "http://localhost:8000/compare?q=wireless%20noise%20cancelling%20headphones&min_rating=4&max_price=500"
```

Tim tren tung engine:

```sh
curl "http://localhost:8000/search/elasticsearch?q=bluetooth%20speaker"
curl "http://localhost:8000/search/meilisearch?q=bluetooth%20speaker"
curl "http://localhost:8000/search/postgres?q=bluetooth%20speaker"
```

Review analytics:

```sh
curl "http://localhost:8000/analytics/reviews"
```

## Vai Tro Tung Engine

Elasticsearch:

- Fuzzy search bang `multi_match` voi `fuzziness: AUTO`
- Synonym analyzer cho cac cum nhu `anc`, `noise cancelling`, `headphones`
- Highlight va aggregation manh

Meilisearch:

- Typo tolerance mac dinh, phu hop demo search UX nhanh
- Facet/filter don gian theo brand, category, price, rating
- Highlight tra ve qua `_formatted`

PostgreSQL Full-Text Search:

- `tsvector`, `websearch_to_tsquery`, `ts_rank`
- `pg_trgm` de bo sung typo/fuzzy matching
- Facet bang `GROUP BY`

## Ghi Chu

Raw dataset lon khong duoc commit. Repo chi commit `data/sample` de demo nhanh.
Neu chay tren VM thay vi may local, nen dung SSH tunnel cho port khong muon public.
