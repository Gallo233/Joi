from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_companion.core import collaboration_store
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

    def test_delegate_is_bounded_by_launch_while_collaborate_is_bounded_by_scope(self) -> None:
        collaborating = self.store.start_session("computer_use", "整理", "collaborate")["session"]["id"]
        self.store.grant_permission(collaborating, "collaborate")
        self.assertTrue(self.store.action_allowed(collaborating, "computer.click")["scope_bound"])

        delegated = self.store.start_session("computer_use", "整理", "delegate")["session"]["id"]
        self.store.grant_permission(delegated, "delegate")
        verdict = self.store.action_allowed(delegated, "computer.click")
        self.assertTrue(verdict["allowed"])
        self.assertFalse(verdict["scope_bound"])
        self.assertTrue(self.store.permission_for_session(delegated)["current_launch"])

        # A grant restored after a Joi restart carries the previous launch id.
        with self.store._connection:
            self.store._connection.execute(
                "UPDATE permission_grants SET launch_id='launch-previous' WHERE session_id=? AND status='active'",
                (delegated,),
            )
        stale = self.store.action_allowed(delegated, "computer.click")
        self.assertFalse(stale["allowed"])
        self.assertEqual(stale["reason"], "delegate_launch_expired")
        self.assertFalse(self.store.permission_for_session(delegated)["current_launch"])

    def test_expanding_scope_is_a_separate_confirmed_step(self) -> None:
        project_id = self.store.context()["project_id"]
        self.store.add_binding(project_id, "application", "Safari")
        session_id = self.store.start_session("computer_use", "查资料", "collaborate")["session"]["id"]
        self.store.grant_permission(session_id, "collaborate")

        preview = self.store.expand_permission(session_id, {"application": ["Mail"], "domain": ["example.com"]})
        self.assertFalse(preview["ok"])
        self.assertEqual(preview["error"], "expand_confirmation_required")
        self.assertEqual(preview["additions"], {"application": ["Mail"], "domain": ["example.com"]})
        # Nothing moved without confirmation.
        self.assertNotIn("Mail", self.store.permission_for_session(session_id)["scope"]["application"])

        confirmed = self.store.expand_permission(session_id, {"application": ["Mail"]}, confirmed=True)
        self.assertTrue(confirmed["changed"])
        scope = self.store.permission_for_session(session_id)["scope"]
        self.assertIn("Mail", scope["application"])
        self.assertIn("Safari", scope["application"])
        # Expanding never changes the profile the user picked.
        self.assertEqual(self.store.permission_for_session(session_id)["profile"], "collaborate")

        repeat = self.store.expand_permission(session_id, {"application": ["Mail"]})
        self.assertTrue(repeat["ok"])
        self.assertFalse(repeat["changed"])

    def test_cold_start_recovers_sessions_left_running_by_a_dead_process(self) -> None:
        running = self.store.start_session("computer_use", "整理", "delegate")["session"]["id"]
        waiting = self.store.start_session("computer_use", "等确认", "collaborate")["session"]["id"]
        self.store.transition_session(waiting, "waiting_approval")
        finished = self.store.start_session("computer_use", "已完成", "observe")["session"]["id"]
        self.store.transition_session(finished, "completed")
        self.store.add_receipt(running, {"action": "computer.click", "status": "completed"})
        workspace, data_home = self.store.workspace, self.store.data_home
        self.store.close()

        # A new Joi process gets a new LAUNCH_ID at import; patching it is what
        # makes this a different launch rather than a second store in the same one.
        with patch.object(collaboration_store, "LAUNCH_ID", "launch-next"):
            self.store = CollaborationStore(workspace, data_home, "character-joi")
            self._assert_recovered(running, waiting, finished)

    def _assert_recovered(self, running: str, waiting: str, finished: str) -> None:
        recovered = self.store.session_payload(running, include_receipts=True)
        self.assertEqual(recovered["state"], "paused")
        self.assertTrue(recovered["recovery_required"])
        self.assertEqual(recovered["pause_reason"], "recovery_required")
        # Recovery must not discard evidence of what already happened.
        self.assertEqual(len(recovered["receipts"]), 1)

        # A decision that was already blocking on a person stays blocking.
        self.assertEqual(self.store.session_payload(waiting, False)["state"], "waiting_approval")
        self.assertEqual(self.store.session_payload(finished, False)["state"], "completed")

        # The delegate grant belonged to the previous launch, so nothing can act on it.
        self.assertEqual(self.store.permission_for_session(running), {})
        verdict = self.store.action_allowed(running, "computer.click")
        self.assertFalse(verdict["allowed"])
        self.assertTrue(verdict["requires_approval"])
        reconciliation = self.store.launch_reconciliation
        self.assertEqual(reconciliation["expired_delegate_grants"], 1)
        self.assertEqual(reconciliation["sessions_recovery_required"], 1)

    def test_resuming_a_recovered_session_clears_the_recovery_flag(self) -> None:
        session_id = self.store.start_session("computer_use", "整理", "collaborate")["session"]["id"]
        workspace, data_home = self.store.workspace, self.store.data_home
        self.store.close()
        with patch.object(collaboration_store, "LAUNCH_ID", "launch-next"):
            self.store = CollaborationStore(workspace, data_home, "character-joi")
        self.assertTrue(self.store.session_payload(session_id, False)["recovery_required"])

        resumed = self.store.transition_session(session_id, "running")["session"]
        self.assertEqual(resumed["state"], "running")
        self.assertFalse(resumed["recovery_required"])
        self.assertEqual(resumed["pause_reason"], "")

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
