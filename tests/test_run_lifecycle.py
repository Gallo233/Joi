"""Phase 1 exit criteria: a crash must never look like a success.

Each test here maps to one of the failure modes TDD §17 Phase 1 requires to be
closed: crash-before-act, act-before-receipt, duplicate-resume, and expired or
mismatched approvals must all refuse to repeat an external effect; one thread
admits one active run; separate threads never borrow each other's identity.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import time
import unittest

from agent_companion.core import collaboration_store
from agent_companion.core.action_intent import ActionIntent, EffectKind
from agent_companion.core.collaboration_store import CollaborationStore
from agent_companion.core.run_coordinator import ResourceBusy, RunCoordinator
from agent_companion.core.run_store import RunContext, canonical_digest
from agent_companion.core.schemas import ToolRequest


class RunLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = CollaborationStore(self.root, self.root / "data", "character-joi")
        self.runs = self.store.runs
        self.coordinator = RunCoordinator(self.runs, owner="test")
        context = self.store.context()
        self.project_id = context["project_id"]
        self.thread_a = context["thread_id"]
        self.thread_b = self.store.create_thread(self.project_id, "第二个对话")["thread"]["id"]

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _run(self, thread_id: str, intent: str = "整理") -> dict:
        created = self.runs.create_run(self.project_id, thread_id, intent, character_id="character-joi")
        self.assertTrue(created["ok"], created)
        return created["run"]

    # ------------------------------------------------- thread serialization

    def test_same_thread_admits_only_one_active_run(self) -> None:
        first = self._run(self.thread_a)
        second = self.runs.create_run(self.project_id, self.thread_a, "另一个目标")
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"], "thread_run_active")
        self.assertEqual(second["active_run_id"], first["id"])

        # Finishing the first frees the conversation.
        self.runs.transition_run(first["id"], "completed")
        self.assertTrue(self.runs.create_run(self.project_id, self.thread_a, "现在可以了")["ok"])

    def test_a_second_conversation_runs_independently(self) -> None:
        self._run(self.thread_a)
        other = self.runs.create_run(self.project_id, self.thread_b, "并行目标")
        self.assertTrue(other["ok"], other)
        self.assertEqual(other["run"]["thread_id"], self.thread_b)

    def test_run_identity_is_frozen_and_does_not_follow_the_active_thread(self) -> None:
        run = self._run(self.thread_b, "在 B 里发起")
        context = self.runs.run_context(run["id"])
        self.assertIsInstance(context, RunContext)
        self.assertEqual(context.thread_id, self.thread_b)

        # The user switches conversations while the run is queued.
        self.store.activate_thread(self.thread_a)
        self.assertEqual(self.store.context()["thread_id"], self.thread_a)

        # The run still belongs to the conversation that started it.
        self.assertEqual(self.runs.run_context(run["id"]).thread_id, self.thread_b)
        with self.assertRaises(Exception):
            context.thread_id = self.thread_a  # frozen dataclass

    def _timed_pair(self, thread_ids: tuple[str, str]) -> list[tuple[float, float]]:
        """Run two callbacks concurrently and report when each actually ran.

        Intervals rather than a shared barrier: work on the same conversation is
        serialized, so anything that makes both bodies wait for each other would
        deadlock by construction.
        """
        spans: dict[int, tuple[float, float]] = {}
        lock = threading.Lock()

        def work(slot: int) -> None:
            begin = time.monotonic()
            time.sleep(0.15)
            with lock:
                spans[slot] = (begin, time.monotonic())

        workers = [
            threading.Thread(target=lambda slot=slot, tid=tid: self.coordinator.run_serial(tid, lambda: work(slot)))
            for slot, tid in enumerate(thread_ids)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertEqual(len(spans), 2)
        return [spans[0], spans[1]]

    @staticmethod
    def _overlaps(first: tuple[float, float], second: tuple[float, float]) -> bool:
        return first[0] < second[1] and second[0] < first[1]

    def test_separate_conversations_overlap(self) -> None:
        first, second = self._timed_pair((self.thread_a, self.thread_b))
        self.assertTrue(self._overlaps(first, second), "different threads should run concurrently")

    def test_one_conversation_stays_strictly_ordered(self) -> None:
        first, second = self._timed_pair((self.thread_a, self.thread_a))
        self.assertFalse(self._overlaps(first, second), "same thread must serialize")

    def test_coordinator_does_not_leak_a_lock_per_conversation(self) -> None:
        for index in range(20):
            self.coordinator.run_serial(f"thread-{index}", lambda: None)
        self.assertEqual(self.coordinator.busy_threads(), 0)

    # --------------------------------------------------------- approvals

    def _challenge(self, run_id: str, request: ToolRequest, scope: dict | None = None) -> dict:
        intent = ActionIntent.from_request(request)
        created = self.runs.create_challenge(
            run_id,
            request.name,
            intent.normalized_args_digest,
            thread_id=self.thread_a,
            effect_kind=intent.effect_kind.value,
            scope_hash=canonical_digest(scope or {}),
        )
        return created["challenge"]

    def test_an_approval_can_only_be_spent_once(self) -> None:
        run = self._run(self.thread_a)
        request = ToolRequest("computer.click", {"x": 4, "y": 5}, "点击下一页")
        intent = ActionIntent.from_request(request)
        challenge = self._challenge(run["id"], request)

        self.assertTrue(self.runs.resolve_challenge(challenge["id"], "approve")["ok"])
        first = self.runs.consume_challenge(challenge["id"], tool=request.name, args_hash=intent.normalized_args_digest)
        self.assertTrue(first["ok"])
        self.assertEqual(first["challenge"]["status"], "consumed")

        second = self.runs.consume_challenge(challenge["id"], tool=request.name, args_hash=intent.normalized_args_digest)
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"], "challenge_already_consumed")

    def test_concurrent_consumers_cannot_both_spend_one_approval(self) -> None:
        run = self._run(self.thread_a)
        request = ToolRequest("computer.click", {"x": 1, "y": 1}, "点击")
        intent = ActionIntent.from_request(request)
        challenge = self._challenge(run["id"], request)
        self.runs.resolve_challenge(challenge["id"], "approve")

        results: list[bool] = []
        gate = threading.Barrier(4, timeout=5)
        lock = threading.Lock()

        def consume() -> None:
            gate.wait()
            outcome = self.runs.consume_challenge(challenge["id"], tool=request.name, args_hash=intent.normalized_args_digest)
            with lock:
                results.append(bool(outcome.get("ok")))

        workers = [threading.Thread(target=consume) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertEqual(results.count(True), 1, f"exactly one consumer may win, got {results}")

    def test_editing_the_step_supersedes_the_old_approval(self) -> None:
        run = self._run(self.thread_a)
        approved = ToolRequest("computer.type_text", {"text": "hello"}, "输入")
        challenge = self._challenge(run["id"], approved)
        self.runs.resolve_challenge(challenge["id"], "approve")

        # The step the user actually approved is not the step now being run.
        edited = ActionIntent.from_request(ToolRequest("computer.type_text", {"text": "goodbye"}, "输入"))
        blocked = self.runs.consume_challenge(challenge["id"], tool="computer.type_text", args_hash=edited.normalized_args_digest)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"], "fingerprint_mismatch")
        self.assertEqual(blocked["challenge"]["status"], "superseded")

    def test_a_different_tool_cannot_reuse_an_approval(self) -> None:
        run = self._run(self.thread_a)
        request = ToolRequest("computer.click", {"x": 2, "y": 2}, "点击")
        intent = ActionIntent.from_request(request)
        challenge = self._challenge(run["id"], request)
        self.runs.resolve_challenge(challenge["id"], "approve")

        swapped = self.runs.consume_challenge(challenge["id"], tool="files.delete", args_hash=intent.normalized_args_digest)
        self.assertFalse(swapped["ok"])
        self.assertEqual(swapped["error"], "fingerprint_mismatch")

    def test_scope_change_after_approval_fails_closed(self) -> None:
        run = self._run(self.thread_a)
        request = ToolRequest("computer.open_app", {"app_name": "Safari"}, "打开 Safari")
        intent = ActionIntent.from_request(request)
        challenge = self._challenge(run["id"], request, scope={"application": ["Safari"]})
        self.runs.resolve_challenge(challenge["id"], "approve")

        moved = self.runs.consume_challenge(
            challenge["id"],
            tool=request.name,
            args_hash=intent.normalized_args_digest,
            scope_hash=canonical_digest({"application": ["Mail"]}),
        )
        self.assertFalse(moved["ok"])
        self.assertEqual(moved["error"], "scope_changed")

    def test_expired_approval_fails_closed(self) -> None:
        run = self._run(self.thread_a)
        request = ToolRequest("computer.click", {"x": 9, "y": 9}, "点击")
        intent = ActionIntent.from_request(request)
        created = self.runs.create_challenge(run["id"], request.name, intent.normalized_args_digest, ttl_seconds=1.0)
        challenge = created["challenge"]
        self.runs.resolve_challenge(challenge["id"], "approve")

        self.assertEqual(self.runs.expire_stale_challenges(now=time.time() + 5), 1)
        stale = self.runs.consume_challenge(challenge["id"], tool=request.name, args_hash=intent.normalized_args_digest)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["error"], "challenge_not_approved")

    def test_a_rejected_approval_is_never_consumable(self) -> None:
        run = self._run(self.thread_a)
        request = ToolRequest("files.delete", {"path": "/tmp/x"}, "删除")
        intent = ActionIntent.from_request(request)
        challenge = self._challenge(run["id"], request)
        self.assertTrue(self.runs.resolve_challenge(challenge["id"], "reject")["ok"])
        self.assertFalse(self.runs.resolve_challenge(challenge["id"], "approve")["ok"])
        denied = self.runs.consume_challenge(challenge["id"], tool=request.name, args_hash=intent.normalized_args_digest)
        self.assertEqual(denied["error"], "challenge_not_approved")

    # ----------------------------------------------------------- effects

    def test_duplicate_resume_cannot_perform_an_effect_twice(self) -> None:
        run = self._run(self.thread_a)
        key = ActionIntent.from_request(ToolRequest("computer.click", {"x": 3, "y": 3})).idempotency_key

        first = self.runs.acquire_effect_lease(run["id"], key, effect_kind=EffectKind.DESKTOP_INPUT.value, owner="worker-1")
        self.assertTrue(first["ok"])
        held = self.runs.acquire_effect_lease(run["id"], key, owner="worker-2")
        self.assertFalse(held["ok"])
        self.assertEqual(held["error"], "effect_lease_held")

        self.runs.mark_effect(first["effect"]["id"], "verified", receipt_id="receipt-1")
        again = self.runs.acquire_effect_lease(run["id"], key, owner="worker-3")
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"], "effect_already_completed")

    def test_act_before_receipt_demands_reconciliation_instead_of_replay(self) -> None:
        run = self._run(self.thread_a)
        key = ActionIntent.from_request(ToolRequest("computer.click", {"x": 7, "y": 7})).idempotency_key
        leased = self.runs.acquire_effect_lease(run["id"], key, owner="worker-1")
        # The action went out; the process died before the receipt landed.
        self.runs.mark_effect(leased["effect"]["id"], "acting")

        retry = self.runs.acquire_effect_lease(run["id"], key, owner="worker-2")
        self.assertFalse(retry["ok"])
        self.assertEqual(retry["error"], "effect_needs_reconciliation")
        self.assertEqual([row["idempotency_key"] for row in self.runs.unreconciled_effects(run["id"])], [key])

    def test_crash_before_act_is_recoverable_without_replaying(self) -> None:
        run = self._run(self.thread_a)
        key = ActionIntent.from_request(ToolRequest("computer.click", {"x": 8, "y": 8})).idempotency_key
        self.runs.save_checkpoint(run["id"], "prepared", {"tool": "computer.click"})
        self.runs.acquire_effect_lease(run["id"], key, owner="worker-1")
        workspace, data_home = self.store.workspace, self.store.data_home
        self.store.close()

        with patch_launch("launch-next"):
            self.store = CollaborationStore(workspace, data_home, "character-joi")

        recovered = self.store.runs.get_run(run["id"])
        self.assertEqual(recovered["state"], "paused")
        self.assertTrue(recovered["recovery_required"])
        # A lease with no receipt is abandoned, never silently re-run.
        self.assertEqual(self.store.runs.effect_by_key(key)["state"], "abandoned")
        self.assertEqual(self.store.runs.latest_checkpoint(run["id"], "prepared")["state"]["tool"], "computer.click")

    def test_reclaiming_an_abandoned_lease_is_a_fresh_attempt(self) -> None:
        run = self._run(self.thread_a)
        key = "explicit-key"
        first = self.runs.acquire_effect_lease(run["id"], key, owner="worker-1")
        self.runs.mark_effect(first["effect"]["id"], "abandoned")
        retaken = self.runs.acquire_effect_lease(run["id"], key, owner="worker-2")
        self.assertTrue(retaken["ok"])
        self.assertTrue(retaken["reclaimed"])
        self.assertEqual(retaken["effect"]["lease_owner"], "worker-2")

    # -------------------------------------------------- launch recovery

    def test_approvals_do_not_survive_the_launch_that_granted_them(self) -> None:
        run = self._run(self.thread_a)
        request = ToolRequest("computer.click", {"x": 6, "y": 6}, "点击")
        intent = ActionIntent.from_request(request)
        challenge = self._challenge(run["id"], request)
        self.runs.resolve_challenge(challenge["id"], "approve")
        workspace, data_home = self.store.workspace, self.store.data_home
        self.store.close()

        with patch_launch("launch-next"):
            self.store = CollaborationStore(workspace, data_home, "character-joi")

        revived = self.store.runs.get_challenge(challenge["id"])
        self.assertEqual(revived["status"], "expired")
        denied = self.store.runs.consume_challenge(challenge["id"], tool=request.name, args_hash=intent.normalized_args_digest)
        self.assertFalse(denied["ok"])
        self.assertEqual(self.store.launch_reconciliation["approvals_expired"], 1)
        self.assertEqual(self.store.launch_reconciliation["runs_recovery_required"], 1)

    def test_a_terminal_run_cannot_be_resurrected(self) -> None:
        run = self._run(self.thread_a)
        self.runs.transition_run(run["id"], "completed")
        revived = self.runs.transition_run(run["id"], "running")
        self.assertFalse(revived["ok"])
        self.assertEqual(revived["error"], "run_already_terminal")

    def test_stale_revision_loses_the_transition(self) -> None:
        run = self._run(self.thread_a)
        self.assertTrue(self.runs.transition_run(run["id"], "running", expected_revision=run["revision"])["ok"])
        conflict = self.runs.transition_run(run["id"], "paused", expected_revision=run["revision"])
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["error"], "state_conflict")

    # -------------------------------------------------- global resources

    def test_concurrent_threads_still_cannot_share_the_desktop(self) -> None:
        first = self._run(self.thread_a)
        second = self.runs.create_run(self.project_id, self.thread_b, "并行")["run"]
        with self.coordinator.exclusive("desktop_input", run_id=first["id"], thread_id=self.thread_a):
            with self.assertRaises(ResourceBusy) as busy:
                with self.coordinator.exclusive("desktop_input", run_id=second["id"], thread_id=self.thread_b):
                    pass
            self.assertEqual(busy.exception.resource, "desktop_input")
        # Released with the block, so the other run can take it.
        with self.coordinator.exclusive("desktop_input", run_id=second["id"], thread_id=self.thread_b) as lease:
            self.assertEqual(lease["run_id"], second["id"])

    def test_finishing_a_run_releases_whatever_it_held(self) -> None:
        run = self._run(self.thread_a)
        self.runs.acquire_resource("microphone", run_id=run["id"], thread_id=self.thread_a, owner="test")
        self.coordinator.finish_run(run["id"], "completed")
        other = self.runs.create_run(self.project_id, self.thread_b, "接着用")["run"]
        self.assertTrue(self.runs.acquire_resource("microphone", run_id=other["id"], owner="test")["ok"])

    def test_unknown_exclusive_resource_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            with self.coordinator.exclusive("gpu"):
                pass

    # ------------------------------------------------------- rpc dedup

    def test_repeated_state_changing_rpc_returns_the_first_result(self) -> None:
        self.runs.remember_rpc("capability.session.pause", "key-1", {"ok": True, "session_id": "s-1"})
        self.runs.remember_rpc("capability.session.pause", "key-1", {"ok": True, "session_id": "s-CHANGED"})
        recalled = self.runs.recall_rpc("capability.session.pause", "key-1")
        self.assertTrue(recalled["found"])
        self.assertEqual(recalled["result"]["session_id"], "s-1")
        self.assertFalse(self.runs.recall_rpc("capability.session.pause", "other-key")["found"])

    # ----------------------------------------------------------- audit

    def test_audit_links_the_whole_chain(self) -> None:
        run = self._run(self.thread_a)
        step = self.runs.add_step(run["id"], 0, "computer.click", effect_kind=EffectKind.DESKTOP_INPUT.value)
        effect = self.runs.acquire_effect_lease(run["id"], "audit-key", step_id=step["id"])["effect"]
        self.runs.record_audit("effect_verified", {"result": "changed"}, run_id=run["id"], step_id=step["id"], effect_id=effect["id"], receipt_id="receipt-9")
        entry = self.runs.list_audit(run["id"])[0]
        self.assertEqual((entry["run_id"], entry["step_id"], entry["effect_id"], entry["receipt_id"]), (run["id"], step["id"], effect["id"], "receipt-9"))
        self.assertEqual(entry["payload"]["result"], "changed")


def patch_launch(value: str):
    from unittest.mock import patch

    return patch.object(collaboration_store, "LAUNCH_ID", value)


if __name__ == "__main__":
    unittest.main()
