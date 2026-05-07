# Nexus ShopX Search Demo

This repo is configured only for the Nexus GCE VM cluster and the Product
Search & Discovery scenario. Elasticsearch/Kibana are pinned to
Elastic `8.17.0`.

| VM | Private IP | Public IP | Services |
| --- | --- | --- | --- |
| `nexus-master-1` | `10.148.0.18` | `34.126.85.104` | Elasticsearch master, PostgreSQL, Kibana |
| `nexus-worker-1` | `10.148.0.16` | none | Elasticsearch data/ingest |
| `nexus-worker-2` | `10.148.0.17` | none | Elasticsearch data/ingest |
| `nexus-worker-3` | `10.148.0.19` | none | Elasticsearch data/ingest |
| `nexus-worker-4` | `10.148.0.20` | none | Elasticsearch data/ingest |

Primary demo interface:

- Kibana Dev Tools: run Elasticsearch DSL scenarios
- Kibana dashboards: inspect analytics and zero-result logs
- Notebook ingest: load Amazon Electronics metadata and create SBERT embeddings

Security is disabled in Elasticsearch because this repo does not include
transport TLS material for cross-host nodes. Keep `9200`, `9300`, and
PostgreSQL private to the VPC or trusted sources. PostgreSQL is bound to
`127.0.0.1:5432` on the master so the ingest notebook can connect from the VM
host without exposing it externally.

## Prepare VMs

The Terraform state shows the VMs as `TERMINATED`; start them in GCP first.

With the current `nexus/infra` bootstrap, this repo is cloned or fast-forwarded
on every VM during startup:

```sh
/opt/nexus/docker-elk
```

The bootstrap does not run Docker Compose automatically. SSH into the VM and
run the stack manually when needed.

Verify on every VM:

```sh
sudo mkdir -p /data/elasticsearch
sudo chown -R 1000:1000 /data/elasticsearch
cd /opt/nexus/docker-elk
cat /etc/nexus-elastic.env
```

Run only on `nexus-master-1`:

```sh
sudo mkdir -p /data/postgres
sudo chown -R 999:999 /data/postgres
```

Optional full dataset path:

```text
data/raw/meta_Electronics.jsonl.gz
```

The notebook ingest requires the Amazon metadata file to exist.

## Start Workers

Run the same command on each worker. The node name, private IP, and
Elasticsearch roles are read from `/etc/nexus-elastic.env`, which is generated
by `nexus/infra` during VM startup.

```sh
docker compose --env-file .env --env-file /etc/nexus-elastic.env up -d elasticsearch
```

## Start Master

Run on `nexus-master-1`:

```sh
docker compose --env-file .env --env-file /etc/nexus-elastic.env --profile master up -d
```

For the new Kibana-first demo, ingest with the notebook:

```text
data/ingest_amazon_electronics.ipynb
```

Install notebook dependencies if needed:

```sh
pip install -r data/requirements.txt
```

The notebook loads `data/raw/meta_Electronics.jsonl.gz` into both PostgreSQL
and Elasticsearch, then embeds products with
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` using 384
dimensions.

Generate query vectors for Kibana semantic search:

```sh
python scripts/generate_query_vector.py "headphones for working out"
python scripts/generate_query_vector.py "tai nghe chong on"
```

Create scalability demo data:

```sh
python scripts/create_demo_scale.py --reset --count 10000
```

Kibana listens on:

```text
http://34.126.85.104:5601
```

The Nexus Terraform firewall includes TCP `5601` for Kibana on the master.

## Verify

Run on the master:

```sh
curl http://10.148.0.18:9200/_cluster/health?pretty
curl "http://10.148.0.18:9200/_cat/nodes?v&h=name,node.role,master,ip"
```

Expected Elasticsearch nodes:

- `nexus-master-1`: master-only
- `nexus-worker-1`: data/ingest
- `nexus-worker-2`: data/ingest
- `nexus-worker-3`: data/ingest
- `nexus-worker-4`: data/ingest

See [SCENARIO.md](SCENARIO.md) for the Act 1-4 demo commands.
Use [kibana/devtools/shopx_demo.es](kibana/devtools/shopx_demo.es) for Kibana
Dev Tools requests.
Use [kibana/dashboard/README.md](kibana/dashboard/README.md) for the optional
zero-result analytics dashboard setup.
Use [data/baseline_postgres.sql](data/baseline_postgres.sql) for Act 1
PostgreSQL baseline queries.

## Stop

On a worker:

```sh
docker compose --env-file .env --env-file /etc/nexus-elastic.env down
```

On the master:

```sh
docker compose --env-file .env --env-file /etc/nexus-elastic.env --profile master down
```
