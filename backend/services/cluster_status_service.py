from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from elasticsearch import Elasticsearch

from backend.config import settings


class ElasticsearchClusterStatusService:
    def __init__(self) -> None:
        self.client = Elasticsearch(settings.elasticsearch_url, request_timeout=30)
        self.elasticsearch_url = settings.elasticsearch_url

    def snapshot(self) -> dict[str, Any]:
        cat_health = self._call(
            "GET _cat/health?v",
            lambda: self.client.cat.health(format="json"),
        )
        cluster_health = self._call(
            "GET _cluster/health?pretty",
            self.client.cluster.health,
        )
        nodes = self._call(
            "GET _cat/nodes?v",
            lambda: self.client.cat.nodes(format="json", h="name,node.role,master,ip"),
        )
        shards = self._call(
            "GET _cat/shards/amazon_electronics_*?v",
            lambda: self.client.cat.shards(
                index="amazon_electronics_*",
                format="json",
                h="index,shard,prirep,state,docs,store,ip,node,unassigned.reason",
            ),
        )
        allocation = self._call(
            "GET _cat/allocation?v",
            lambda: self.client.cat.allocation(format="json"),
        )
        recovery = self._call(
            "GET _cat/recovery/amazon_electronics_*?v",
            lambda: self.client.cat.recovery(index="amazon_electronics_*", format="json"),
        )

        calls = {
            "cat_health": cat_health,
            "cluster_health": cluster_health,
            "nodes": nodes,
            "shards": shards,
            "allocation": allocation,
            "recovery": recovery,
        }
        errors = {
            call["label"]: call["error"]
            for call in calls.values()
            if not call["ok"]
        }

        allocation_explain = self._allocation_explain()
        if allocation_explain["status"] == "error":
            errors["GET _cluster/allocation/explain"] = allocation_explain["message"]

        cluster_health_data = self._data(cluster_health, {})
        cat_health_data = self._data(cat_health, [])
        node_rows = self._data(nodes, [])
        shard_rows = self._data(shards, [])
        recovery_rows = self._data(recovery, [])

        return {
            "elasticsearch_url": self.elasticsearch_url,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": self._summary(
                cluster_health_data,
                cat_health_data,
                node_rows,
                shard_rows,
                recovery_rows,
                errors,
            ),
            "cat_health": cat_health_data,
            "cluster_health": cluster_health_data,
            "nodes": node_rows,
            "shards": shard_rows,
            "allocation": self._data(allocation, []),
            "allocation_explain": allocation_explain,
            "recovery": recovery_rows,
            "errors": errors,
            "probes": [
                {
                    "label": call["label"],
                    "ok": call["ok"],
                    "error": call.get("error"),
                }
                for call in calls.values()
            ],
        }

    def _allocation_explain(self) -> dict[str, Any]:
        try:
            body = self._plain(self.client.cluster.allocation_explain(include_disk_info=True))
        except Exception as exc:
            message = self._error_message(exc)
            if "unable to find any unassigned shards" in message.lower():
                return {
                    "status": "no_unassigned_shards",
                    "message": "No unassigned shards need allocation explanation.",
                }
            return {
                "status": "error",
                "message": message,
                "status_code": self._status_code(exc),
            }
        return {"status": "ok", "body": body}

    def _summary(
        self,
        cluster_health: dict[str, Any],
        cat_health: list[dict[str, Any]],
        nodes: list[dict[str, Any]],
        shards: list[dict[str, Any]],
        recovery: list[dict[str, Any]],
        errors: dict[str, str],
    ) -> dict[str, Any]:
        cat_status = cat_health[0].get("status") if cat_health else None
        status = cluster_health.get("status") or cat_status
        if not status:
            status = "unreachable" if errors else "unknown"

        state_counts = Counter(str(row.get("state") or "unknown") for row in shards)
        recovery_active = [
            row
            for row in recovery
            if str(row.get("stage") or "").lower() not in {"", "done"}
        ]
        index_names = {
            str(row.get("index"))
            for row in shards
            if row.get("index")
        }

        return {
            "cluster_name": cluster_health.get("cluster_name") or self._cat_value(cat_health, "cluster"),
            "status": status,
            "node_count": self._int(cluster_health.get("number_of_nodes"), len(nodes)),
            "data_node_count": self._int(
                cluster_health.get("number_of_data_nodes"),
                self._data_node_count(nodes),
            ),
            "active_primary_shards": self._int(cluster_health.get("active_primary_shards")),
            "active_shards": self._int(cluster_health.get("active_shards")),
            "relocating_shards": self._int(cluster_health.get("relocating_shards")),
            "initializing_shards": self._int(cluster_health.get("initializing_shards")),
            "unassigned_shards": self._int(cluster_health.get("unassigned_shards")),
            "delayed_unassigned_shards": self._int(cluster_health.get("delayed_unassigned_shards")),
            "active_shards_percent": self._float(
                cluster_health.get("active_shards_percent_as_number")
            ),
            "demo_index_count": len(index_names),
            "demo_shard_count": len(shards),
            "primary_demo_shards": sum(1 for row in shards if row.get("prirep") == "p"),
            "replica_demo_shards": sum(1 for row in shards if row.get("prirep") == "r"),
            "shard_state_counts": dict(sorted(state_counts.items())),
            "recovery_total": len(recovery),
            "recovery_active": len(recovery_active),
        }

    def _call(self, label: str, request: Callable[[], Any]) -> dict[str, Any]:
        try:
            return {"label": label, "ok": True, "data": self._plain(request())}
        except Exception as exc:
            return {
                "label": label,
                "ok": False,
                "error": self._error_message(exc),
                "status_code": self._status_code(exc),
            }

    def _plain(self, value: Any) -> Any:
        body = getattr(value, "body", None)
        if body is not None:
            value = body
        if isinstance(value, dict):
            return {key: self._plain(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._plain(item) for item in value]
        return value

    def _data(self, call: dict[str, Any], fallback: Any) -> Any:
        if call["ok"]:
            return call["data"]
        return fallback

    def _data_node_count(self, nodes: list[dict[str, Any]]) -> int:
        return sum(1 for node in nodes if "d" in str(node.get("node.role") or ""))

    def _cat_value(self, rows: list[dict[str, Any]], key: str) -> Any:
        if not rows:
            return None
        return rows[0].get(key)

    def _int(self, value: Any, default: int = 0) -> int:
        if value is None:
            return default
        try:
            return int(float(str(value).strip().rstrip("%")))
        except ValueError:
            return default

    def _float(self, value: Any) -> float:
        if value is None:
            return 0.0
        try:
            return float(str(value).strip().rstrip("%"))
        except ValueError:
            return 0.0

    def _status_code(self, exc: Exception) -> int | None:
        status_code = getattr(exc, "status_code", None)
        if status_code is not None:
            return int(status_code)
        meta = getattr(exc, "meta", None)
        status = getattr(meta, "status", None)
        if status is not None:
            return int(status)
        return None

    def _error_message(self, exc: Exception) -> str:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                reason = error.get("reason")
                if reason:
                    return str(reason)
            if isinstance(error, str):
                return error
        status_code = self._status_code(exc)
        if status_code is None:
            return str(exc)
        return f"[{status_code}] {exc}"
