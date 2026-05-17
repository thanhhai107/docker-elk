from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from backend.config import settings


NodeAction = Literal["start", "stop", "restart"]


@dataclass(frozen=True)
class ControlTarget:
    id: str
    host: str
    label: str


class ElasticsearchClusterControlService:
    def __init__(self) -> None:
        self.targets = self._parse_targets(settings.elasticsearch_control_targets)

    def config(self) -> dict[str, Any]:
        ssh_key_available = self._ssh_key_available()
        return {
            "enabled": settings.elasticsearch_control_enabled,
            "targets": [
                {"id": target.id, "host": target.host, "label": target.label}
                for target in self.targets
            ],
            "compose_dir": settings.elasticsearch_control_compose_dir,
            "compose_env_files": self._compose_env_files(),
            "ssh_user": settings.elasticsearch_control_ssh_user,
            "ssh_port": settings.elasticsearch_control_ssh_port,
            "ssh_key_available": ssh_key_available,
            "configured": bool(
                settings.elasticsearch_control_enabled
                and self.targets
                and ssh_key_available
            ),
        }

    def run(self, target_id: str, action: NodeAction) -> dict[str, Any]:
        if not settings.elasticsearch_control_enabled:
            raise RuntimeError("Elasticsearch cluster control is disabled.")

        target = self._target(target_id)
        remote_command = self._remote_command(action)
        command = self._ssh_command(target, remote_command)

        started_at = datetime.now(timezone.utc)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=settings.elasticsearch_control_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "target": target.id,
                "action": action,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "returncode": None,
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or f"Command timed out after {exc.timeout} seconds.",
                "command": self._display_command(target, remote_command),
            }

        return {
            "ok": completed.returncode == 0,
            "target": target.id,
            "action": action,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "command": self._display_command(target, remote_command),
        }

    def _target(self, target_id: str) -> ControlTarget:
        for target in self.targets:
            if target.id == target_id:
                return target
        raise KeyError(target_id)

    def _parse_targets(self, raw_targets: str) -> list[ControlTarget]:
        targets = []
        for raw_target in raw_targets.split(","):
            item = raw_target.strip()
            if not item:
                continue
            if "=" in item:
                target_id, host = [part.strip() for part in item.split("=", 1)]
            else:
                target_id = item
                host = item
            if not target_id or not host:
                continue
            targets.append(ControlTarget(id=target_id, host=host, label=target_id))
        return targets

    def _ssh_command(self, target: ControlTarget, remote_command: str) -> list[str]:
        ssh_target = target.host
        if settings.elasticsearch_control_ssh_user:
            ssh_target = f"{settings.elasticsearch_control_ssh_user}@{target.host}"

        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-p",
            str(settings.elasticsearch_control_ssh_port),
        ]
        if settings.elasticsearch_control_ssh_key:
            command.extend(["-i", settings.elasticsearch_control_ssh_key])
        command.extend([ssh_target, remote_command])
        return command

    def _ssh_key_available(self) -> bool:
        if not settings.elasticsearch_control_ssh_key:
            return True
        return Path(settings.elasticsearch_control_ssh_key).is_file()

    def _remote_command(self, action: NodeAction) -> str:
        compose_dir = shlex.quote(settings.elasticsearch_control_compose_dir)
        env_args = " ".join(
            f"--env-file {shlex.quote(env_file)}"
            for env_file in self._compose_env_files()
        )
        compose = "docker compose"
        if env_args:
            compose = f"{compose} {env_args}"
        return f"cd {compose_dir} && {compose} {action} elasticsearch"

    def _display_command(self, target: ControlTarget, remote_command: str) -> str:
        ssh_target = target.host
        if settings.elasticsearch_control_ssh_user:
            ssh_target = f"{settings.elasticsearch_control_ssh_user}@{target.host}"
        return f"ssh {ssh_target} {shlex.quote(remote_command)}"

    def _compose_env_files(self) -> list[str]:
        return [
            item.strip()
            for item in settings.elasticsearch_control_compose_env_files.split(",")
            if item.strip()
        ]
