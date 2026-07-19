from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_companion.core.collaboration_store import CollaborationStore, DEFAULT_PROJECT_ID, DEFAULT_THREAD_ID
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, VoiceLine


class CollaborationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.data_home = self.workspace / "app-data"
        self.store = CollaborationStore(self.workspace, self.data_home, "character-joi")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_project_thread_context_and_event_isolation(self) -> None:
        created = self.store.create_project("工作", "character-joi")
        project_id = created["project"]["id"]
        thread_id = created["thread"]["id"]
        context = self.store.context()
        self.assertEqual((context["project_id"], context["thread_id"]), (project_id, thread_id))

        event = AgentEvent(EventType.USER_MESSAGE, "task-1", DisplayCard("你", "整理资料"), VoiceLine(""))
        self.store.record_event(event)

        self.assertEqual(len(self.store.history(thread_id)), 1)
        self.assertEqual(self.store.history(DEFAULT_THREAD_ID), [])

    def test_session_permission_profiles_and_sensitive_guardrail(self) -> None:
        started = self.store.start_session("computer_use", "整理备忘录", "delegate")
        session_id = started["session"]["id"]

        self.assertTrue(self.store.action_allowed(session_id, "computer.click")["allowed"])
        self.assertTrue(self.store.action_allowed(session_id, "delete")["requires_approval"])

        paused = self.store.transition_session(session_id, "paused")
        self.assertEqual(paused["session"]["state"], "paused")

        receipt = self.store.add_receipt(session_id, {"action": "computer.click", "status": "completed", "verification": {"status": "changed"}})
        self.assertEqual(receipt["receipt"]["step_index"], 1)
        self.assertEqual(self.store.list_receipts(session_id)[0]["verification"]["status"], "changed")

    def test_legacy_events_are_backed_up_and_imported_once(self) -> None:
        self.store.close()
        legacy_dir = self.workspace / "data" / "agent_companion"
        legacy_dir.mkdir(parents=True)
        legacy_path = legacy_dir / "events.jsonl"
        legacy_path.write_text(json.dumps({"event_id": "legacy-1", "sequence": 1, "type": "user_message", "task_id": "old", "created_at": 1, "display_card": {"summary": "旧对话"}, "agent_state": {}}) + "\n", encoding="utf-8")
        self.store = CollaborationStore(self.workspace, self.workspace / "other-data")

        self.assertEqual(self.store.history(DEFAULT_THREAD_ID)[0]["event_id"], "legacy-1")
        self.assertTrue(legacy_path.with_suffix(".jsonl.pre-sqlite-backup").is_file())

    def test_delete_requires_archive_and_confirmation(self) -> None:
        created = self.store.create_project("临时")
        project_id = created["project"]["id"]

        self.assertEqual(self.store.delete_project(project_id)["error"], "archive_and_confirm_required")
        self.store.update_project(project_id, archived=True)
        self.assertTrue(self.store.delete_project(project_id, confirmed=True)["ok"])
        self.assertEqual(self.store.context()["project_id"], DEFAULT_PROJECT_ID)


if __name__ == "__main__":
    unittest.main()
