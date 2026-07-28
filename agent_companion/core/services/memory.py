from __future__ import annotations

from typing import Any

from agent_companion.core.coercion import bool_or, optional_int
from agent_companion.core.memory import MemoryStore


class MemoryService:
    """Stable command boundary around the long-term memory store."""

    DEFAULT_RECALL_LIMIT = 8
    MAX_RECALL_LIMIT = 20
    DEFAULT_LIST_LIMIT = 20
    MAX_LIST_LIMIT = 50

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def status(self) -> dict[str, Any]:
        return {"ok": True, "memory": self.store.status()}

    def recall(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        values = params if isinstance(params, dict) else {}
        query = str(values.get("query") or "")
        requested_limit = optional_int(values.get("limit"))
        limit = min(self.MAX_RECALL_LIMIT, max(1, requested_limit or self.DEFAULT_RECALL_LIMIT))
        return {
            "ok": True,
            "memories": self.store.recall(
                query,
                limit,
                project_id=str(values.get("project_id") or ""),
                thread_id=str(values.get("thread_id") or ""),
            ),
        }

    def list(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        values = params if isinstance(params, dict) else {}
        requested_limit = optional_int(values.get("limit"))
        requested_offset = optional_int(values.get("offset"))
        limit = min(self.MAX_LIST_LIMIT, max(1, requested_limit or self.DEFAULT_LIST_LIMIT))
        offset = max(0, requested_offset or 0)
        return {
            "ok": True,
            "page": self.store.list_memories(
                query=str(values.get("query") or ""),
                kind=str(values.get("kind") or ""),
                offset=offset,
                limit=limit,
                sort=str(values.get("sort") or "recent"),
            ),
        }

    def pending(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        values = params if isinstance(params, dict) else {}
        requested_limit = optional_int(values.get("limit"))
        requested_offset = optional_int(values.get("offset"))
        limit = min(self.MAX_LIST_LIMIT, max(1, requested_limit or self.DEFAULT_LIST_LIMIT))
        offset = max(0, requested_offset or 0)
        return {"ok": True, "page": self.store.pending_page(offset=offset, limit=limit)}

    def browse_vault(self) -> dict[str, Any]:
        return {
            "ok": True,
            "vault": self.store.browse_vault(),
            "memory": self.store.status(),
        }

    def save_candidate(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        candidate_id = self._id_from(params, "candidate_id")
        if candidate_id is None:
            return self._missing_id("missing_candidate_id")
        return self._with_status(self.store.save_candidate(candidate_id))

    def reject_candidate(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        values = params if isinstance(params, dict) else {}
        candidate_id = optional_int(values.get("candidate_id"))
        if candidate_id is None:
            return self._missing_id("missing_candidate_id")
        reason = str(values.get("reason") or "user_rejected")
        return self._with_status(self.store.reject_candidate(candidate_id, reason))

    def set_enabled(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        values = params if isinstance(params, dict) else {}
        return {"ok": True, "memory": self.store.set_enabled(bool_or(values.get("enabled"), True))}

    def delete(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        memory_id = self._id_from(params, "memory_id")
        if memory_id is None:
            return self._missing_id("missing_memory_id")
        return self._with_status(self.store.delete(memory_id))

    def update(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        values = params if isinstance(params, dict) else {}
        memory_id = optional_int(values.get("memory_id"))
        if memory_id is None:
            return self._missing_id("missing_memory_id")
        result = self.store.update(
            memory_id,
            text=str(values.get("text") or ""),
            kind=str(values.get("kind") or "") or None,
        )
        return self._with_status(result)

    def clear(self) -> dict[str, Any]:
        return self._with_status(self.store.clear())

    def _missing_id(self, error: str) -> dict[str, Any]:
        return {"ok": False, "error": error, "memory": self.store.status()}

    def _with_status(self, result: dict[str, Any]) -> dict[str, Any]:
        return {**result, "memory": self.store.status()}

    @staticmethod
    def _id_from(params: dict[str, Any] | None, key: str) -> int | None:
        values = params if isinstance(params, dict) else {}
        return optional_int(values.get(key))
