"""Per-thread serialization for runs.

One global lock made every conversation wait behind every other, and it also
meant a queued task could pick up whichever thread the user had since switched
to. Serializing per `thread_id` fixes both: within a conversation work stays
strictly ordered, while separate conversations proceed independently.

Concurrency across conversations is not permission to fight over the machine.
There is one desktop, one microphone and one speaker, so those stay behind
explicit leases held in SQLite (TDD §6.6, ADR-010).
"""

from __future__ import annotations

from contextlib import contextmanager
import threading
import time
from typing import Any, Callable, Iterator

from agent_companion.core.run_store import EXCLUSIVE_RESOURCES, RunStore


class CancellationToken:
    """Cooperative cancel, propagated into planners, providers and tools.

    An external action already sent cannot be recalled; cancelling stops the
    next step and still requires the current one to be observed and receipted
    (TDD §6.6).
    """

    __slots__ = ("_event", "reason")

    def __init__(self) -> None:
        self._event = threading.Event()
        self.reason = ""

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str = "user_cancelled") -> None:
        self.reason = reason or "cancelled"
        self._event.set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


class ResourceBusy(RuntimeError):
    def __init__(self, resource: str, holder: dict[str, Any]) -> None:
        super().__init__(f"{resource} is held by another run")
        self.resource = resource
        self.holder = holder


class RunCoordinator:
    def __init__(self, store: RunStore, owner: str = "") -> None:
        self.store = store
        self.owner = owner or f"coordinator-{id(self):x}"
        self._registry_lock = threading.Lock()
        self._thread_locks: dict[str, threading.RLock] = {}
        self._waiters: dict[str, int] = {}
        self._tokens: dict[str, CancellationToken] = {}
        self._sequence = 0

    def _thread_lock(self, thread_id: str) -> threading.RLock:
        key = thread_id or "__global__"
        with self._registry_lock:
            lock = self._thread_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._thread_locks[key] = lock
            self._waiters[key] = self._waiters.get(key, 0) + 1
            return lock

    def _release_thread_lock(self, thread_id: str) -> None:
        key = thread_id or "__global__"
        with self._registry_lock:
            remaining = self._waiters.get(key, 1) - 1
            if remaining > 0:
                self._waiters[key] = remaining
                return
            # Nobody else is queued on this conversation, so drop the entry
            # rather than growing a lock per thread the user ever opened.
            self._waiters.pop(key, None)
            self._thread_locks.pop(key, None)

    def run_serial(self, thread_id: str, callback: Callable[[], Any]) -> tuple[int, Any]:
        """Run `callback` with this conversation held exclusively.

        Returns a monotonic sequence so callers can order responses, matching
        the guarantee the previous global lock provided.
        """
        lock = self._thread_lock(thread_id)
        try:
            with lock:
                with self._registry_lock:
                    self._sequence += 1
                    sequence = self._sequence
                return sequence, callback()
        finally:
            self._release_thread_lock(thread_id)

    def busy_threads(self) -> int:
        with self._registry_lock:
            return len(self._thread_locks)

    # ------------------------------------------------------------- cancel

    def token(self, run_id: str) -> CancellationToken:
        with self._registry_lock:
            token = self._tokens.get(run_id)
            if token is None:
                token = CancellationToken()
                self._tokens[run_id] = token
            return token

    def cancel_run(self, run_id: str, reason: str = "user_cancelled") -> bool:
        with self._registry_lock:
            token = self._tokens.get(run_id)
        if token is None:
            return False
        token.cancel(reason)
        return True

    def forget_run(self, run_id: str) -> None:
        with self._registry_lock:
            self._tokens.pop(run_id, None)

    # ------------------------------------------------------------ leases

    @contextmanager
    def exclusive(self, resource: str, *, run_id: str = "", thread_id: str = "", ttl_seconds: float = 300.0) -> Iterator[dict[str, Any]]:
        """Hold a global OS resource for the duration of the block."""
        if resource not in EXCLUSIVE_RESOURCES:
            raise ValueError(f"unknown exclusive resource: {resource}")
        acquired = self.store.acquire_resource(resource, run_id=run_id, thread_id=thread_id, owner=self.owner, ttl_seconds=ttl_seconds)
        if not acquired.get("ok"):
            raise ResourceBusy(resource, acquired.get("lease") or {})
        try:
            yield acquired.get("lease") or {}
        finally:
            self.store.release_resource(resource, run_id=run_id)

    # -------------------------------------------------------------- runs

    def begin_run(
        self,
        project_id: str,
        thread_id: str,
        intent: str = "",
        *,
        character_id: str = "",
        session_id: str = "",
        capability_id: str = "",
        launch_id: str = "",
    ) -> dict[str, Any]:
        """Create a run, or report the one already active on this thread."""
        result = self.store.create_run(
            project_id,
            thread_id,
            intent,
            character_id=character_id,
            session_id=session_id,
            capability_id=capability_id,
            launch_id=launch_id,
        )
        if result.get("ok"):
            self.token(result["run"]["id"])
        return result

    def finish_run(self, run_id: str, state: str, *, pause_reason: str = "") -> dict[str, Any]:
        result = self.store.transition_run(run_id, state, pause_reason=pause_reason)
        if result.get("ok") and state in {"completed", "failed", "cancelled"}:
            self.forget_run(run_id)
            for resource in EXCLUSIVE_RESOURCES:
                self.store.release_resource(resource, run_id=run_id)
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._registry_lock:
            return {
                "owner": self.owner,
                "sequence": self._sequence,
                "serialized_threads": sorted(self._thread_locks),
                "tracked_runs": sorted(self._tokens),
                "observed_at": time.time(),
            }
