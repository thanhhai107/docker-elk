from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def gcp_project_id() -> str:
    value = os.getenv("GCP_PROJECT_ID", "").strip()
    if value:
        return value

    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not credentials_path:
        return ""

    try:
        credentials = json.loads(Path(credentials_path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""

    project_id = credentials.get("project_id")
    return project_id.strip() if isinstance(project_id, str) else ""


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    elasticsearch_url: str = os.getenv("ELASTICSEARCH_URL", "http://127.0.0.1:9200")
    meili_url: str = os.getenv("MEILI_URL", "http://127.0.0.1:7700")
    meili_master_key: str = os.getenv("MEILI_MASTER_KEY", "masterKey")
    postgres_dsn: str = os.getenv(
        "POSTGRES_DSN", "postgresql://search:search_demo@127.0.0.1:5432/amazon_search"
    )
    data_dir: Path = Path(os.getenv("DATA_DIR", "data"))
    gcp_project_id: str = gcp_project_id()
    gcp_location: str = os.getenv("GCP_LOCATION", "us-central1")
    elasticsearch_control_enabled: bool = env_bool("ELASTICSEARCH_CONTROL_ENABLED")
    elasticsearch_control_targets: str = os.getenv("ELASTICSEARCH_CONTROL_TARGETS", "")
    elasticsearch_control_ssh_user: str = os.getenv("ELASTICSEARCH_CONTROL_SSH_USER", "")
    elasticsearch_control_ssh_key: str = os.getenv("ELASTICSEARCH_CONTROL_SSH_KEY", "")
    elasticsearch_control_ssh_port: int = int(os.getenv("ELASTICSEARCH_CONTROL_SSH_PORT", "22"))
    elasticsearch_control_compose_dir: str = os.getenv(
        "ELASTICSEARCH_CONTROL_COMPOSE_DIR", "/opt/nexus/docker-elk"
    )
    elasticsearch_control_compose_env_files: str = os.getenv(
        "ELASTICSEARCH_CONTROL_COMPOSE_ENV_FILES", ".env,/etc/nexus-elastic.env"
    )
    elasticsearch_control_timeout_seconds: int = int(os.getenv("ELASTICSEARCH_CONTROL_TIMEOUT_SECONDS", "90"))


settings = Settings()
