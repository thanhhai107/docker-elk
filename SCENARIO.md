# ShopX Demo Scenario

Primary demo interface: Kibana Dev Tools.

Use this file for the talk track and use
`kibana/devtools/shopx_demo.es` for the Elasticsearch DSL commands.

## Prepare

Start the Nexus cluster and master services:

```sh
docker compose --env-file .env --env-file /etc/nexus-elastic.env --profile master up -d
```

Ingest Amazon Electronics metadata with SBERT embeddings:

```text
data/ingest_amazon_electronics.ipynb
```

The notebook reads:

```text
data/raw/meta_Electronics.jsonl.gz
```

It loads PostgreSQL and Elasticsearch with the same product corpus, creates
`shopx_products`, `shopx_users`, `shopx_logs`, and `shopx_eval`, and embeds
products with `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
using 384 dimensions.

## Act 1 - PostgreSQL Baseline

Goal: show the pain before showing Elasticsearch.

Run these through PostgreSQL over the same `products` table loaded by the
notebook. Use `psql` on `nexus-master-1`:

```sh
docker compose --env-file .env --env-file /etc/nexus-elastic.env --profile master exec postgres psql -U shopx -d shopx
```

The same SQL is saved in:

```text
data/baseline_postgres.sql
```

### 1.1 Synonym Failure

```sql
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%noise cancelling headphones%'
   OR description ILIKE '%noise cancelling headphones%'
ORDER BY product_id
LIMIT 5;
```

```sql
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%ANC headphones%'
   OR description ILIKE '%ANC headphones%'
ORDER BY product_id
LIMIT 5;
```

Expected point: PostgreSQL does not know `ANC` means `active noise cancellation`.

### 1.2 Semantic Failure

```sql
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%headphones for working out%'
   OR description ILIKE '%headphones for working out%'
ORDER BY product_id
LIMIT 5;
```

Expected point: PostgreSQL matches characters, not intent. It misses sport,
gym, running, and athletic products.

### 1.3 Ranking Failure

```sql
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%bluetooth speaker%'
   OR description ILIKE '%bluetooth speaker%'
ORDER BY product_id
LIMIT 5;
```

Expected point: matching products can be returned in a non-business ranking.

### 1.4 Typo Failure

```sql
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%samsug galxy s24%'
   OR description ILIKE '%samsug galxy s24%'
ORDER BY product_id
LIMIT 5;
```

Expected point: typo produces zero or poor results.

## Act 2 - Elasticsearch Built-ins and AI Model

Open Kibana Dev Tools:

```text
http://34.126.85.104:5601/app/dev_tools#/console
```

Paste and run sections from:

```text
kibana/devtools/shopx_demo.es
```

### Part A - Built-in Elasticsearch

Run these sections:

- `Act 2A.1 - Fuzzy search`
- `Act 2A.2 - Search-as-you-type`
- `Act 2A.3 - Relevance scoring`
- `Act 2A.4 - Filter + aggregation`
- `Act 2A.5 - Complete built-in experience`

Expected point: ES handles typo, prefix typing, ranking, filtering, and
aggregation in one search engine without an AI model.

### Part B - AI Model + Elasticsearch

Run:

- `Act 2B.1 - Synonym graph`
- `Act 2B.2 - Semantic vector search`
- `Act 2B.2 extra - Cross-language semantic search`

For semantic sections, generate the query vector first:

```sh
python scripts/generate_query_vector.py "headphones for working out"
python scripts/generate_query_vector.py "tai nghe chong on"
```

Paste the generated vector into `PASTE_VECTOR_HERE` in Dev Tools.

Expected point: ES built-ins are strong, but semantic intent and cross-language
matching need vector embeddings.

### Personalization If Time

Run:

- `Act 2B.3 - Personalization: Audiophile Minh`
- `Act 2B.3 - Personalization: Budget Hunter Nam`

Expected point: same query, same catalog, different ranking.

## Act 3 - Scalability

Create the scale index:

```sh
python scripts/create_demo_scale.py --reset --count 10000
```

In Kibana Dev Tools, run the `Act 3 - Scalability` section from
`kibana/devtools/shopx_demo.es`.

The written scenario says 3 nodes A/B/C. The Nexus cluster has 1 master-only
node plus 4 data/ingest workers, so the same demo runs on 4 data nodes. Stop one
worker node for the resilience step.

Demo steps:

1. Show `demo_scale` shard distribution with `_cat/shards`.
2. Stop one Elasticsearch node VM/container.
3. Run `GET /demo_scale/_search` and show data is still searchable.
4. Restart or add a node and show shard recovery/relocation.

Expected point: sharding distributes load, replicas provide resilience, and
nodes can be added without downtime.

## Act 4 - Zero-Result Analytics

Run the `Business analytics - zero-result tracking example` section in
`kibana/devtools/shopx_demo.es`.

Expected point: search is not only retrieval. Logs tell the business where
users fail to find products and what vocabulary/catalog gaps to fix.
