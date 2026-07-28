from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from agent_companion.core.coercion import bool_or, float_or, optional_int
from agent_companion.core.codex_support import permission_fingerprint
from agent_companion.core.services import ArtifactService, BackgroundContextService, MemoryService


class FakeMemoryStore:
    def __init__(self) -> None:
        self.enabled = True
        self.last_recall: tuple[str, int] | None = None

    def status(self) -> dict[str, object]:
        return {"enabled": self.enabled}

    def recall(self, query: str, limit: int, *, project_id: str = "", thread_id: str = "") -> list[dict[str, object]]:
        self.last_recall = (query, limit)
        self.last_recall_scope = (project_id, thread_id)
        return [{"id": 1, "text": query}]

    def list_memories(self, **values: object) -> dict[str, object]:
        return {"items": [], "total": 0, "offset": values.get("offset", 0), "limit": values.get("limit", 20), "has_more": False}

    def browse_vault(self) -> dict[str, object]:
        return {"saved": []}

    def save_candidate(self, candidate_id: int) -> dict[str, object]:
        return {"ok": True, "saved_id": candidate_id}

    def reject_candidate(self, candidate_id: int, reason: str) -> dict[str, object]:
        return {"ok": True, "rejected_id": candidate_id, "reason": reason}

    def set_enabled(self, enabled: bool) -> dict[str, object]:
        self.enabled = enabled
        return self.status()

    def delete(self, memory_id: int) -> dict[str, object]:
        return {"ok": True, "deleted_id": memory_id}

    def update(self, memory_id: int, *, text: str, kind: str | None = None) -> dict[str, object]:
        return {"ok": True, "memory": {"id": memory_id, "text": text, "kind": kind or "note"}}

    def clear(self) -> dict[str, object]:
        return {"ok": True, "cleared": True}


class FakeBackgroundStore:
    def __init__(self) -> None:
        self.state: dict[str, object] = {"enabled": False}

    def status(self) -> dict[str, object]:
        return dict(self.state)

    def configure(self, **values: object) -> dict[str, object]:
        self.state.update({key: value for key, value in values.items() if value not in (None, "")})
        return {"ok": True, "background": self.status()}

    def clear_context(self) -> dict[str, object]:
        self.state["recent_context"] = []
        return {"ok": True, "background": self.status()}


class CoercionTests(unittest.TestCase):
    def test_shared_coercion_has_stable_fallbacks(self) -> None:
        self.assertEqual(optional_int("12"), 12)
        self.assertIsNone(optional_int("bad"))
        self.assertEqual(float_or("1.5", 0.0), 1.5)
        self.assertEqual(float_or("bad", 2.0), 2.0)
        self.assertTrue(bool_or("开启"))
        self.assertFalse(bool_or("off", True))

    def test_permission_fingerprint_is_deterministic(self) -> None:
        first = permission_fingerprint({"tool": "command", "nested": {"b": 2, "a": 1}})
        second = permission_fingerprint({"nested": {"a": 1, "b": 2}, "tool": "command"})

        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)


class MemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeMemoryStore()
        self.service = MemoryService(self.store)  # type: ignore[arg-type]

    def test_recall_clamps_limit(self) -> None:
        result = self.service.recall({"query": "Joi", "limit": 999})

        self.assertTrue(result["ok"])
        self.assertEqual(self.store.last_recall, ("Joi", 20))

    def test_candidate_commands_validate_ids(self) -> None:
        self.assertEqual(self.service.save_candidate({})["error"], "missing_candidate_id")
        self.assertEqual(self.service.delete({})["error"], "missing_memory_id")
        self.assertEqual(self.service.reject_candidate({"candidate_id": "4"})["rejected_id"], 4)

    def test_list_clamps_page_size(self) -> None:
        result = self.service.list({"offset": 4, "limit": 999})

        self.assertTrue(result["ok"])
        self.assertEqual(result["page"]["offset"], 4)
        self.assertEqual(result["page"]["limit"], 50)

    def test_boolean_setting_uses_shared_coercion(self) -> None:
        result = self.service.set_enabled({"enabled": "关闭"})

        self.assertTrue(result["ok"])
        self.assertFalse(result["memory"]["enabled"])


class BackgroundContextServiceTests(unittest.TestCase):
    def test_configuration_and_clear_are_always_audited(self) -> None:
        store = FakeBackgroundStore()
        audits: list[tuple[str, dict[str, object], str]] = []
        service = BackgroundContextService(  # type: ignore[arg-type]
            store,
            lambda summary, background, status: audits.append((summary, background, status)),
        )

        configured = service.configure({"enabled": True, "scope_type": "application"})
        cleared = service.clear()

        self.assertTrue(configured["ok"])
        self.assertTrue(cleared["ok"])
        self.assertEqual([audit[2] for audit in audits], ["success", "info"])


class ArtifactServiceTests(unittest.TestCase):
    def test_reads_workspace_image_and_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            image = workspace / "preview.png"
            image.write_bytes(b"png-data")
            service = ArtifactService(workspace)

            result = service.read_image("preview.png")

            self.assertTrue(result["ok"])
            self.assertEqual(result["data_url"], "data:image/png;base64," + base64.b64encode(b"png-data").decode("ascii"))
            self.assertEqual(service.read_image("../outside.png")["error"], "artifact_not_found")

    def test_rejects_non_image_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "notes.txt").write_text("hello", encoding="utf-8")

            self.assertEqual(ArtifactService(workspace).read_image("notes.txt")["error"], "unsupported_artifact_type")


if __name__ == "__main__":
    unittest.main()
