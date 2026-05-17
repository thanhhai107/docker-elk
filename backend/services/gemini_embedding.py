from __future__ import annotations

import time
from typing import Any

import google.generativeai as genai

from backend.config import settings

EMBEDDING_MODEL = "models/text-embedding-004"
EMBEDDING_DIMS = 768
BATCH_SIZE = 100
MAX_RETRIES = 5
RETRY_BASE_DELAY = 2.0

_configured = False


def _ensure_configured() -> None:
    global _configured
    if _configured:
        return
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    genai.configure(api_key=settings.gemini_api_key)
    _configured = True


def embed_query(text: str) -> list[float]:
    _ensure_configured()
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=text,
        task_type="retrieval_query",
        output_dimensionality=EMBEDDING_DIMS,
    )
    return result["embedding"]


def embed_texts(texts: list[str], task_type: str = "retrieval_document") -> list[list[float]]:
    _ensure_configured()
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        embeddings = _embed_batch_with_retry(batch, task_type)
        all_embeddings.extend(embeddings)
    return all_embeddings


def _embed_batch_with_retry(batch: list[str], task_type: str) -> list[list[float]]:
    for attempt in range(MAX_RETRIES):
        try:
            result = genai.embed_content(
                model=EMBEDDING_MODEL,
                content=batch,
                task_type=task_type,
                output_dimensionality=EMBEDDING_DIMS,
            )
            return result["embedding"]
        except Exception as exc:
            if attempt == MAX_RETRIES - 1:
                raise
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            if "429" in str(exc) or "quota" in str(exc).lower() or "rate" in str(exc).lower():
                delay = max(delay, 60)
            time.sleep(delay)
    return []
