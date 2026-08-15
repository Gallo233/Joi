from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping


_MAX_RECENT_GOALS = 12
_MAX_SUMMARY = 500


def _world_key(server_id: str, world: str) -> str:
    return f"{str(server_id).strip().casefold()}\0{str(world).strip().casefold()}"


def _bounded(value: Any, limit: int) -> str:
    text = str(value or "")
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    return text.strip()[:limit]


class MinecraftWorldMemory:
    """Private, per-world memory across sessions (M2).

    Only the sanitized summary may cross into prompts: dimension, vitals and
    recent goal labels. Raw checkpoints stay under data/private with the
    existing 0600/0700 permissions, and coordinates never enter this store's
    summary projection.
    """

    def __init__(self, data_home: Path) -> None:
        self._path = Path(data_home) / "private" / "minecraft-memory.json"
        self._lock = threading.RLock()
        self._store: dict[str, dict[str, Any]] = self._load()

    def remember(
        self,
        server_id: str,
        world: str,
        *,
        observation: Mapping[str, Any] | None,
        recent_goals: list[str] | None,
    ) -> None:
        key = _world_key(server_id, world)
        observation = observation if isinstance(observation, Mapping) else {}
        world_view = observation.get("world") if isinstance(observation.get("world"), Mapping) else {}
        row = self._store.setdefault(key, {})
        row.update(
            {
                "server_id": _bounded(server_id, 80),
                "world": _bounded(world, 80),
                "last_dimension": _bounded(observation.get("dimension"), 24),
                "last_health": int(observation.get("health") or 0),
                "last_food": int(observation.get("food") or 0),
                "last_time_of_day": int(world_view.get("time_of_day") or 0),
                "recent_goals": [_bounded(goal, 80) for goal in (recent_goals or [])][-_MAX_RECENT_GOALS:],
                "updated_at": time.time(),
            }
        )
        self._save()

    def summary(self, server_id: str, world: str) -> str:
        row = self._store.get(_world_key(server_id, world))
        if not row:
            return ""
        parts = [
            f"上次在世界维度 {row.get('last_dimension') or '未知'}，血量 {int(row.get('last_health') or 0)}、饥饿 {int(row.get('last_food') or 0)}"
        ]
        goals = [str(goal) for goal in (row.get("recent_goals") or [])][-5:]
        if goals:
            parts.append("最近完成：" + "、".join(goals))
        return _bounded("；".join(parts), _MAX_SUMMARY)

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._path.parent.chmod(0o700)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._store, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(self._path)
            self._path.chmod(0o600)
        except OSError:
            return
