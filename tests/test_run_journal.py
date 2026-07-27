"""The runtime actually writes to the run record, and the record actually binds.

`tests/test_run_lifecycle.py` proves the store enforces its invariants when
called correctly. These tests prove `AgentCompanionApp` calls it -- that a real
plan produces a run, that the approval the shell receives is the persisted
challenge, and that spending it is what gates execution.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_companion.core import collaboration_store
from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.collaboration_store import CollaborationStore
from agent_companion.core.run_coordinator import RunCoordinator
from agent_companion.core.run_journal import RunJournal, StoreRunJournal
from agent_companion.core.schemas import DisplayCard, EventType, ToolResult, VoiceLine


def _approval_id(events: list) -> str:
    for event in reversed(events):
        approval = (event.agent_state or {}).get("approval")
        if isinstance(approval, dict) and approval.get("approval_id"):
            return str(approval["approval_id"])
    return ""


class RunJournalIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.store = CollaborationStore(self.workspace, self.workspace / "data", "character-joi")
        self.coordinator = RunCoordinator(self.store.runs, owner="test")
        self.app = AgentCompanionApp(self.workspace)
        self.app.set_run_journal(StoreRunJournal(self.store.runs, self.coordinator, self.store.context))
        # The approval path under test resolves to a real desktop action. Stub
        # the execution itself so these tests exercise the journal rather than
        # opening applications on whatever machine runs them; everything before
        # and after the tool call stays real.
        self.executed: list[str] = []
        patcher = patch.object(AgentCompanionApp, "_run_tool_safely", autospec=True, side_effect=self._fake_tool)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_tool(self, _app: AgentCompanionApp, request) -> ToolResult:
        self.executed.append(request.name)
        return ToolResult(
            ok=True,
            agent_state={"tool": request.name},
            display_card=DisplayCard("测试工具", "已执行"),
            voice_line=VoiceLine(""),
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _thread_id(self) -> str:
        return self.store.context()["thread_id"]

    def test_a_plan_opens_and_closes_a_run(self) -> None:
        self.app.handle_user_text("你好")
        runs = self.store.runs
        thread_id = self._thread_id()
        # Chat completes without approval, so the run must not be left open.
        self.assertEqual(runs.active_run(thread_id), {})
        self.assertEqual(self.coordinator.snapshot()["tracked_runs"], [])

    def test_an_approval_is_persisted_and_is_what_the_shell_resolves(self) -> None:
        events = self.app.handle_user_text("帮我在电脑上打开备忘录")
        approval_id = _approval_id(events)
        self.assertTrue(approval_id, "expected an approval to be requested")

        # The id handed to the shell is the durable challenge, not a value that
        # only exists in this process.
        challenge = self.store.runs.get_challenge(approval_id)
        self.assertTrue(challenge, f"{approval_id} should be a persisted challenge")
        self.assertEqual(challenge["status"], "pending")
        self.assertEqual(challenge["thread_id"], self._thread_id())

        run = self.store.runs.get_run(challenge["run_id"])
        self.assertEqual(run["state"], "waiting_approval")
        self.assertEqual(self.store.runs.latest_checkpoint(run["id"], "interrupt")["state"]["challenge_id"], approval_id)

    def test_approving_spends_the_challenge_exactly_once(self) -> None:
        events = self.app.handle_user_text("帮我在电脑上打开备忘录")
        approval_id = _approval_id(events)
        self.app.resolve_approval(approval_id, approved=True)

        challenge = self.store.runs.get_challenge(approval_id)
        self.assertEqual(challenge["status"], "consumed")
        self.assertIsNotNone(challenge["consumed_at"])

        # Replaying the same approval cannot run the step again.
        replay = self.app.resolve_approval(approval_id, approved=True)
        self.assertFalse(any(event.type == EventType.TOOL_STARTED for event in replay))

    def test_rejecting_records_the_decision_and_runs_nothing(self) -> None:
        events = self.app.handle_user_text("帮我在电脑上打开备忘录")
        approval_id = _approval_id(events)
        after = self.app.resolve_approval(approval_id, approved=False)

        self.assertEqual(self.store.runs.get_challenge(approval_id)["status"], "rejected")
        self.assertFalse(any(event.type == EventType.TOOL_STARTED for event in after))
        self.assertTrue(any(event.type == EventType.TASK_FAILED for event in after))

    def test_an_effect_bearing_step_records_a_lease(self) -> None:
        events = self.app.handle_user_text("帮我在电脑上打开备忘录")
        approval_id = _approval_id(events)
        challenge = self.store.runs.get_challenge(approval_id)
        run_id = challenge["run_id"]
        self.app.resolve_approval(approval_id, approved=True)

        self.assertEqual(self.executed, ["computer.workflow"], "the approved step should have run once")
        steps = self.store.runs.list_steps(run_id)
        self.assertTrue(steps, "the executed step should be recorded")
        self.assertTrue(any(row["effect_kind"] != "none" for row in steps), "a desktop action is an effect-bearing step")
        # The effect reached a terminal state, so nothing is left dangling.
        self.assertEqual(self.store.runs.unreconciled_effects(run_id), [])
        self.assertEqual(self.store.runs.get_run(run_id)["state"], "completed")

    def _restart(self) -> CollaborationStore:
        workspace, data_home = self.store.workspace, self.store.data_home
        self.store.close()
        with patch.object(collaboration_store, "LAUNCH_ID", "launch-next"):
            reopened = CollaborationStore(workspace, data_home, "character-joi")
        self.store = reopened
        self.addCleanup(reopened.close)
        return reopened

    def test_a_pending_approval_survives_a_restart_and_stays_decidable(self) -> None:
        approval_id = _approval_id(self.app.handle_user_text("帮我在电脑上打开备忘录"))
        reopened = self._restart()

        # It was blocking on a person before the crash and still is; downgrading
        # it would silently discard the user's pending decision (TDD §7.4).
        challenge = reopened.runs.get_challenge(approval_id)
        self.assertEqual(challenge["status"], "pending")
        self.assertEqual(reopened.runs.get_run(challenge["run_id"])["state"], "waiting_approval")

    def test_an_approval_granted_before_a_crash_is_not_spendable_after_it(self) -> None:
        approval_id = _approval_id(self.app.handle_user_text("帮我在电脑上打开备忘录"))
        # The user approved, then the process died before the step ran.
        self.store.runs.resolve_challenge(approval_id, "approve")
        reopened = self._restart()

        self.assertEqual(reopened.runs.get_challenge(approval_id)["status"], "expired")
        spent = reopened.runs.consume_challenge(approval_id, tool="computer.open_app", args_hash="anything")
        self.assertFalse(spent["ok"])

    def test_the_server_injects_a_real_journal(self) -> None:
        # A no-op journal is the right default for a standalone app, but the
        # server must never ship with one.
        import inspect

        from agent_companion.core import server

        source = inspect.getsource(server.JsonRpcBridge.__init__)
        self.assertIn("set_run_journal", source)
        self.assertIn("StoreRunJournal(", source)

    def test_the_default_journal_keeps_a_standalone_app_working(self) -> None:
        standalone = AgentCompanionApp(self.workspace)
        self.assertIsInstance(standalone.run_journal, RunJournal)
        self.assertFalse(standalone.run_journal.enabled)
        self.assertTrue(standalone.handle_user_text("你好"))


if __name__ == "__main__":
    unittest.main()
