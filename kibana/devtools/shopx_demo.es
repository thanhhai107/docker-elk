# ShopX Product Search & Discovery demo
# Paste this file into Kibana Dev Tools.
#
# Before running semantic queries, generate a query vector on nexus-master-1:
#   python scripts/generate_query_vector.py "<query text>"
#
# For Act 3 scalability data:
#   python scripts/create_demo_scale.py --reset --count 10000


### Cluster checks

GET /_cluster/health?pretty

GET /_cat/nodes?v&h=name,ip,node.role,master,heap.percent,ram.percent,cpu,load_1m

GET /_cat/indices/shopx_*?v


### Act 2A.1 - Fuzzy search fixes typo

GET /shopx_products/_search
{
  "size": 5,
  "_source": ["product_id", "title", "brand", "price", "rating", "review_count"],
  "query": {
    "multi_match": {
      "query": "<typo query>",
      "fields": ["title^4", "brand.text^2", "category.text^2", "description"],
      "fuzziness": "AUTO",
      "prefix_length": 1
    }
  }
}


### Act 2A.2 - Search-as-you-type

GET /shopx_products/_search
{
  "size": 5,
  "_source": ["product_id", "title", "brand", "price", "rating"],
  "query": {
    "multi_match": {
      "query": "<prefix query>",
      "type": "bool_prefix",
      "fields": [
        "title_suggest",
        "title_suggest._2gram",
        "title_suggest._3gram"
      ]
    }
  }
}


### Act 2A.3 - Relevance scoring

GET /shopx_products/_search
{
  "size": 5,
  "_source": ["product_id", "title", "brand", "price", "rating", "review_count"],
  "query": {
    "function_score": {
      "query": {
        "multi_match": {
          "query": "<search query>",
          "fields": ["title^4", "brand.text^2", "category.text^2", "description"]
        }
      },
      "functions": [
        {
          "field_value_factor": {
            "field": "rating",
            "factor": 0.35,
            "missing": 0
          }
        },
        {
          "field_value_factor": {
            "field": "review_count",
            "factor": 0.18,
            "modifier": "log1p",
            "missing": 0
          }
        }
      ],
      "score_mode": "sum",
      "boost_mode": "sum"
    }
  }
}


### Act 2A.4 - Filter + aggregation in one request

GET /shopx_products/_search
{
  "size": 10,
  "_source": ["product_id", "title", "brand", "category", "price", "rating"],
  "query": {
    "bool": {
      "must": [
        {
          "multi_match": {
            "query": "<search query>",
            "fields": ["title^4", "brand.text^2", "category.text^2", "description"]
          }
        }
      ],
      "filter": [
        { "range": { "price": { "lte": 200 } } }
      ]
    }
  },
  "aggs": {
    "brands": { "terms": { "field": "brand", "size": 10 } },
    "categories": { "terms": { "field": "category", "size": 10 } },
    "avg_price": { "avg": { "field": "price" } },
    "price_ranges": {
      "range": {
        "field": "price",
        "ranges": [
          { "to": 50 },
          { "from": 50, "to": 100 },
          { "from": 100, "to": 300 },
          { "from": 300 }
        ]
      }
    }
  }
}


### Act 2A.5 - Complete built-in experience: fuzzy + prefix + scoring + aggregations

GET /shopx_products/_search
{
  "size": 5,
  "_source": ["product_id", "title", "brand", "category", "price", "rating", "review_count"],
  "query": {
    "function_score": {
      "query": {
        "bool": {
          "should": [
            {
              "multi_match": {
                "query": "<search query>",
                "fields": ["title^4", "brand.text^2", "category.text^2", "description"],
                "fuzziness": "AUTO",
                "prefix_length": 1
              }
            },
            {
              "multi_match": {
                "query": "<search query>",
                "type": "bool_prefix",
                "fields": ["title_suggest", "title_suggest._2gram", "title_suggest._3gram"]
              }
            }
          ],
          "minimum_should_match": 1
        }
      },
      "functions": [
        { "field_value_factor": { "field": "rating", "factor": 0.35, "missing": 0 } },
        { "field_value_factor": { "field": "review_count", "factor": 0.18, "modifier": "log1p", "missing": 0 } }
      ],
      "score_mode": "sum",
      "boost_mode": "sum"
    }
  },
  "aggs": {
    "brands": { "terms": { "field": "brand", "size": 10 } },
    "avg_price": { "avg": { "field": "price" } }
  }
}


### Act 2B.1 - Synonym graph

GET /shopx_products/_search
{
  "size": 5,
  "_source": ["product_id", "title", "brand", "price", "rating", "review_count"],
  "query": {
    "multi_match": {
      "query": "<synonym query>",
      "fields": ["title^4", "brand.text^2", "category.text^2", "description"]
    }
  }
}


### Act 2B.2 - Semantic vector search
# Replace PASTE_VECTOR_HERE with output from:
# python scripts/generate_query_vector.py "<semantic query>"

GET /shopx_products/_search
{
  "size": 5,
  "_source": ["product_id", "title", "brand", "price", "rating", "review_count"],
  "query": {
    "multi_match": {
      "query": "<semantic query>",
      "fields": ["title^4", "brand.text^2", "category.text^2", "description"]
    }
  },
  "knn": {
    "field": "embedding",
    "query_vector": PASTE_VECTOR_HERE,
    "k": 10,
    "num_candidates": 100,
    "boost": 5,
    "filter": {
      "terms": { "semantic_terms": ["<semantic term>", "<semantic term>"] }
    }
  }
}


### Act 2B.2 fallback - Semantic script_score search
# Use this if your Elasticsearch build rejects top-level knn + query.

GET /shopx_products/_search
{
  "size": 5,
  "_source": ["product_id", "title", "brand", "price", "rating", "review_count"],
  "query": {
    "script_score": {
      "query": {
        "terms": { "semantic_terms": ["<semantic term>", "<semantic term>"] }
      },
      "script": {
        "source": "Math.max(cosineSimilarity(params.query_vector, 'embedding'), 0) * 5",
        "params": {
          "query_vector": PASTE_VECTOR_HERE
        }
      }
    }
  }
}


### Act 2B.2 extra - Cross-language semantic search
# Replace PASTE_VECTOR_HERE with output from:
# python scripts/generate_query_vector.py "<cross-language query>"

GET /shopx_products/_search
{
  "size": 5,
  "_source": ["product_id", "title", "brand", "price", "rating", "review_count"],
  "query": {
    "multi_match": {
      "query": "<cross-language query>",
      "fields": ["title^4", "brand.text^2", "category.text^2", "description"]
    }
  },
  "knn": {
    "field": "embedding",
    "query_vector": PASTE_VECTOR_HERE,
    "k": 10,
    "num_candidates": 100,
    "boost": 5,
    "filter": {
      "terms": { "semantic_terms": ["<semantic term>", "<semantic term>"] }
    }
  }
}


### Act 2B.2 extra fallback - Cross-language script_score search

GET /shopx_products/_search
{
  "size": 5,
  "_source": ["product_id", "title", "brand", "price", "rating", "review_count"],
  "query": {
    "script_score": {
      "query": {
        "terms": { "semantic_terms": ["<semantic term>", "<semantic term>"] }
      },
      "script": {
        "source": "Math.max(cosineSimilarity(params.query_vector, 'embedding'), 0) * 5",
        "params": {
          "query_vector": PASTE_VECTOR_HERE
        }
      }
    }
  }
}


### Act 2B.3 - Personalization: Audiophile Minh

GET /shopx_products/_search
{
  "size": 5,
  "_source": ["product_id", "title", "brand", "price", "rating", "review_count"],
  "query": {
    "function_score": {
      "query": {
        "multi_match": {
          "query": "<personalized query>",
          "fields": ["title^4", "brand.text^2", "category.text^2", "description"]
        }
      },
      "functions": [
        { "filter": { "terms": { "brand": ["Sony", "Bose", "Sennheiser", "Beats", "Jabra"] } }, "weight": 4 },
        { "filter": { "term": { "category": "Headphones" } }, "weight": 2 },
        { "gauss": { "price": { "origin": 250, "scale": 150, "decay": 0.5 } }, "weight": 2 },
        { "field_value_factor": { "field": "rating", "factor": 0.25, "missing": 0 } }
      ],
      "score_mode": "sum",
      "boost_mode": "sum"
    }
  }
}


### Act 2B.3 - Personalization: Budget Hunter Nam

GET /shopx_products/_search
{
  "size": 5,
  "_source": ["product_id", "title", "brand", "price", "rating", "review_count"],
  "query": {
    "function_score": {
      "query": {
        "multi_match": {
          "query": "<personalized query>",
          "fields": ["title^4", "brand.text^2", "category.text^2", "description"]
        }
      },
      "functions": [
        { "filter": { "terms": { "brand": ["Anker", "JLab", "AmazonBasics", "Mpow"] } }, "weight": 4 },
        { "gauss": { "price": { "origin": 35, "scale": 30, "decay": 0.5 } }, "weight": 3 },
        { "field_value_factor": { "field": "rating", "factor": 0.25, "missing": 0 } }
      ],
      "score_mode": "sum",
      "boost_mode": "sum"
    }
  }
}


### Business analytics - zero-result tracking example

POST /shopx_logs/_doc
{
  "query": "<zero-result query>",
  "user_id": "anonymous",
  "engine": "elasticsearch",
  "result_count": 0,
  "is_zero_result": true,
  "took_ms": 12,
  "timestamp": "2026-05-04T00:00:00Z"
}

GET /shopx_logs/_search
{
  "size": 0,
  "query": { "term": { "is_zero_result": true } },
  "aggs": {
    "queries": {
      "terms": {
        "field": "query.keyword",
        "size": 20,
        "order": { "_count": "desc" }
      }
    }
  }
}


### Act 3 - Scalability
# First create docs:
# python scripts/create_demo_scale.py --reset --count 10000

GET /_cat/indices/demo_scale?v

GET /_cat/shards/demo_scale?v

GET /demo_scale/_search
{
  "track_total_hits": true,
  "query": { "match_all": {} }
}

# Stop one ES node from its VM, then rerun:
GET /_cluster/health/demo_scale?pretty

GET /demo_scale/_search
{
  "track_total_hits": true,
  "query": { "match_all": {} }
}

# After adding or restarting a node, watch relocation:
GET /_cat/recovery/demo_scale?v

GET /_cat/shards/demo_scale?v
