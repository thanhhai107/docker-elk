from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    elasticsearch_url: str = os.getenv("ELASTICSEARCH_URL", "http://127.0.0.1:9200")
    meili_url: str = os.getenv("MEILI_URL", "http://127.0.0.1:7700")
    meili_master_key: str = os.getenv("MEILI_MASTER_KEY", "masterKey")
    postgres_dsn: str = os.getenv(
        "POSTGRES_DSN", "postgresql://search:search_demo@127.0.0.1:5432/amazon_search"
    )
    data_dir: Path = Path(os.getenv("DATA_DIR", "data"))


settings = Settings()
