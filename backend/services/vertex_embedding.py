from __future__ import annotations

import time
from typing import Any

import vertexai
from vertexai.language_models import TextEmbeddingModel

from backend.config import settings

EMBEDDING_MODEL = "text-embedding-004"
EMBEDDING_DIMS = 768
BATCH_SIZE = 100
MAX_RETRIES = 5
RETRY_BASE_DELAY = 2.0

_configured = False
_model: TextEmbeddingModel | None = None


def _ensure_configured() -> None:
    global _configured, _model
    if _configured:
        return
    if not settings.gcp_project_id:
        raise RuntimeError("GCP_PROJECT_ID is not set. Vertex AI requires a Project ID.")
    
    vertexai.init(project=settings.gcp_project_id, location=settings.gcp_location)
    _model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL)
    _configured = True


def embed_query(text: str) -> list[float]:
    _ensure_configured()
    # "RETRIEVAL_QUERY" type is appropriate for queries in search scenarios
    # vertexai SDK currently might not accept output_dimensionality easily via this method,
    # but text-embedding-004 defaults to 768.
    result = _model.get_embeddings([text])
    return result[0].values


import datetime

def embed_texts(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    _ensure_configured()
    all_embeddings: list[list[float]] = []
    total = len(texts)
    for i in range(0, total, BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        embeddings = _embed_batch_with_retry(batch, task_type)
        all_embeddings.extend(embeddings)
        
        # Log progress every 10 batches (1000 products)
        if len(all_embeddings) % 1000 == 0 or len(all_embeddings) == total:
            pct = (len(all_embeddings) / total) * 100
            timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            print(f"[{timestamp}] Vertex AI Progress: {len(all_embeddings)}/{total} ({pct:.1f}%)", flush=True)
            
    return all_embeddings


def _embed_batch_with_retry(batch: list[str], task_type: str) -> list[list[float]]:
    for attempt in range(MAX_RETRIES):
        try:
            # Vertex AI batch embeddings
            results = _model.get_embeddings(batch)
            return [res.values for res in results]
        except Exception as exc:
            if attempt == MAX_RETRIES - 1:
                raise
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            if "429" in str(exc) or "quota" in str(exc).lower() or "rate" in str(exc).lower():
                delay = max(delay, 60)
            time.sleep(delay)
    return []
