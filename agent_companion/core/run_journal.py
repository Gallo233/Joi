"""The bridge between the runtime and the persisted run record.

`AgentCompanionApp` owns planning and execution; `RunStore` owns the durable
record of what was allowed and what happened. This is the seam between them, so
the runtime never imports the store and the store never learns about planners.

The default implementation does nothing. That is deliberate: the app is
constructed on its own in many places (tools, tests, one-off harnesses) where
there is no database, and a single no-op object keeps one code path through
`app.py` instead of a `if self.journal is not None` at every call site. The
server always injects the real one -- `test_run_journal.py` holds it to that.
"""

from __future__ import annotations

from typing import Any, Callable

from agent_companion.core.action_intent import ActionIntent, EffectKind
from agent_companion.core.run_coordinator import RunCoordinator
from agent_companion.core.run_store import RunStore, canonical_digest


class RunJournal:
    """No-op journal: execution proceeds, nothing is written down."""

    enabled = False

    def begin_run(self, task_id: str, intent: str, user_text: str = "") -> str:
        return ""

    def record_step(self, run_id: str, index: int, intent: ActionIntent) -> str:
        return ""

    def open_challenge(self, run_id: str, step_id: str, intent: ActionIntent, *, payload: dict[str, Any] | None = None, scope: dict[str, Any] | None = None, ttl_seconds: float = 600.0) -> str:
        return ""

    def decide_challenge(self, challenge_id: str, approved: bool) -> dict[str, Any]:
        return {"ok": True, "skipped": True}

    def spend_challenge(self, challenge_id: str, intent: ActionIntent, *, scope: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, "skipped": True}

    def begin_effect(self, run_id: str, step_id: str, intent: ActionIntent, *, approval_id: str = "") -> dict[str, Any]:
        return {"ok": True, "skipped": True, "effect": {}}

    def complete_effect(self, effect_id: str, *, ok: bool, receipt_id: str = "") -> None:
        return None

    def record_step_outcome(self, step_id: str, ok: bool) -> None:
        return None

    def finish_run(self, run_id: str, state: str, *, pause_reason: str = "") -> None:
        return None

    def audit(self, kind: str, payload: dict[str, Any] | None = None, **links: str) -> None:
        return None


class StoreRunJournal(RunJournal):
    """Writes the run record into schema v3."""

    enabled = True

    def __init__(self, store: RunStore, coordinator: RunCoordinator, context_provider: Callable[[], dict[str, Any]]) -> None:
        self.store = store
        self.coordinator = coordinator
        self._context_provider = context_provider

    def _context(self) -> dict[str, Any]:
        try:
            return dict(self._context_provider() or {})
        except Exception:
            return {}

    def begin_run(self, task_id: str, intent: str, user_text: str = "") -> str:
        """Create the run, freezing identity from the context at this moment.

        Everything after this uses the returned run id, so switching the active
        conversation mid-flight cannot re-target work already underway.
        """
        context = self._context()
        thread_id = str(context.get("thread_id") or "")
        project_id = str(context.get("project_id") or "")
        if not thread_id or not project_id:
            return ""
        created = self.coordinator.begin_run(
            project_id,
            thread_id,
            user_text or intent,
            character_id=str(context.get("character_id") or ""),
            session_id=str(context.get("session_id") or ""),
            capability_id=intent,
        )
        if not created.get("ok"):
            # A run is already active on this conversation. The caller keeps
            # executing -- refusing the user's message outright is a product
            # decision that belongs above this layer -- but nothing is recorded
            # against a run this work does not own.
            self.audit("run_rejected_thread_busy", {"task_id": task_id, "active_run_id": created.get("active_run_id", "")})
            return ""
        run_id = str(created["run"]["id"])
        self.store.transition_run(run_id, "running")
        self.store.save_checkpoint(run_id, "plan", {"task_id": task_id, "intent": intent})
        return run_id

    def record_step(self, run_id: str, index: int, intent: ActionIntent) -> str:
        if not run_id:
            return ""
        step = self.store.add_step(
            run_id,
            index,
            intent.tool,
            effect_kind=intent.effect_kind.value,
            canonical_args_hash=intent.normalized_args_digest,
        )
        return str(step.get("id") or "")

    def open_challenge(self, run_id: str, step_id: str, intent: ActionIntent, *, payload: dict[str, Any] | None = None, scope: dict[str, Any] | None = None, ttl_seconds: float = 600.0) -> str:
        if not run_id:
            return ""
        context = self._context()
        created = self.store.create_challenge(
            run_id,
            intent.tool,
            intent.normalized_args_digest,
            step_id=step_id,
            thread_id=str(context.get("thread_id") or ""),
            effect_kind=intent.effect_kind.value,
            scope_hash=canonical_digest(scope or {}),
            payload=payload or {},
            ttl_seconds=ttl_seconds,
        )
        challenge_id = str((created.get("challenge") or {}).get("id") or "")
        if challenge_id:
            if step_id:
                self.store.set_step_state(step_id, "waiting_approval")
            self.store.transition_run(run_id, "waiting_approval")
            self.store.save_checkpoint(run_id, "interrupt", {"challenge_id": challenge_id, "tool": intent.tool}, step_id=step_id)
        return challenge_id

    def decide_challenge(self, challenge_id: str, approved: bool) -> dict[str, Any]:
        if not challenge_id:
            return {"ok": True, "skipped": True}
        return self.store.resolve_challenge(challenge_id, "approve" if approved else "reject")

    def spend_challenge(self, challenge_id: str, intent: ActionIntent, *, scope: dict[str, Any] | None = None) -> dict[str, Any]:
        """Consume the approval immediately before acting.

        Re-verified here rather than at decision time because the gap between
        the two is exactly where a plan can be edited or a scope revoked.
        """
        if not challenge_id:
            return {"ok": True, "skipped": True}
        context = self._context()
        return self.store.consume_challenge(
            challenge_id,
            tool=intent.tool,
            args_hash=intent.normalized_args_digest,
            scope_hash=canonical_digest(scope) if scope is not None else "",
            thread_id=str(context.get("thread_id") or ""),
        )

    def begin_effect(self, run_id: str, step_id: str, intent: ActionIntent, *, approval_id: str = "") -> dict[str, Any]:
        """Take the lease for one external effect, or report why not."""
        if not run_id or intent.effect_kind is EffectKind.NONE:
            return {"ok": True, "skipped": True, "effect": {}}
        leased = self.store.acquire_effect_lease(
            run_id,
            intent.idempotency_key,
            step_id=step_id,
            approval_id=approval_id,
            effect_kind=intent.effect_kind.value,
            owner=self.coordinator.owner,
        )
        if leased.get("ok"):
            effect_id = str((leased.get("effect") or {}).get("id") or "")
            if step_id:
                self.store.set_step_state(step_id, "prepared")
            self.store.save_checkpoint(run_id, "prepared", {"tool": intent.tool, "effect_id": effect_id}, step_id=step_id)
            self.store.mark_effect(effect_id, "acting")
        return leased

    def complete_effect(self, effect_id: str, *, ok: bool, receipt_id: str = "") -> None:
        if not effect_id:
            return
        self.store.mark_effect(effect_id, "verified" if ok else "failed", receipt_id=receipt_id)

    def record_step_outcome(self, step_id: str, ok: bool) -> None:
        if step_id:
            self.store.set_step_state(step_id, "verified" if ok else "failed")

    def finish_run(self, run_id: str, state: str, *, pause_reason: str = "") -> None:
        if not run_id:
            return
        self.coordinator.finish_run(run_id, state, pause_reason=pause_reason)

    def audit(self, kind: str, payload: dict[str, Any] | None = None, **links: str) -> None:
        self.store.record_audit(kind, payload or {}, **links)
