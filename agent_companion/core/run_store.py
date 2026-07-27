"""Schema v3: the persistent record of what Joi ran, was allowed to run, and did.

Everything here exists so a crash is never indistinguishable from a success.
Before any external effect Joi writes down what it is about to do; after the
effect it writes down what happened. A process that dies between the two leaves
a row that says "prepared, no receipt", which is a question for the user rather
than something to retry.

Three rules shape the tables:

- An approval is a persisted one-shot transaction, not a UI boolean. It carries
  the fingerprints it was granted against so a restarted Joi can prove the thing
  it is about to do is still the thing that was approved (TDD §7.3, ADR-004).
- Every external effect takes a lease under compare-and-swap. Two callers with
  the same idempotency key cannot both proceed, so a duplicate resume is a
  no-op rather than a second charge (TDD §7.2).
- Audit is one queryable ledger. Receipts, approvals and effects link into it
  rather than forming parallel truths (ADR-011).

The connection and lock are owned by CollaborationStore: these tables reference
its projects/threads, so they must live in the same database and transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
import threading
import time
from typing import Any, Iterable
import uuid

from agent_companion.core.action_intent import EffectKind


RUN_SCHEMA_VERSION = 3

RUN_STATES = ("created", "running", "waiting_approval", "paused", "completed", "failed", "cancelled")
TERMINAL_RUN_STATES = frozenset({"completed", "failed", "cancelled"})
ACTIVE_RUN_STATES = frozenset({"created", "running", "waiting_approval"})

STEP_STATES = ("planned", "waiting_approval", "prepared", "executed", "verified", "failed", "skipped", "superseded")

# A challenge is created pending and leaves exactly once.
CHALLENGE_STATES = ("pending", "approved", "rejected", "expired", "cancelled", "superseded", "consumed")

EFFECT_STATES = ("leased", "acting", "observed", "verified", "failed", "abandoned")

DEFAULT_APPROVAL_TTL_SECONDS = 600.0
DEFAULT_LEASE_TTL_SECONDS = 300.0

# Global OS resources that cannot be shared by concurrent threads even though
# the runs themselves are allowed to interleave (TDD §6.6).
EXCLUSIVE_RESOURCES = ("desktop_input", "microphone", "speaker")


def canonical_digest(payload: Any) -> str:
    """Stable digest used for every fingerprint in this module."""
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        encoded = repr(payload)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RunContext:
    """Identity frozen when a run is created.

    Reading "the currently active thread" at execution time is how a queued run
    ends up attributing its events, approvals and permissions to whatever
    conversation the user happens to be looking at. Every async boundary passes
    this instead (TDD §6.5, ADR-010).
    """

    project_id: str
    thread_id: str
    run_id: str
    character_id: str = ""
    session_id: str = ""
    capability_id: str = ""
    schema_version: int = 1

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "capability_id": self.capability_id,
            "character_id": self.character_id,
        }


class RunStore:
    """Schema v3 persistence. Shares CollaborationStore's connection and lock."""

    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock) -> None:
        self._connection = connection
        self._lock = lock
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    thread_id TEXT NOT NULL REFERENCES conversation_threads(id) ON DELETE CASCADE,
                    character_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    capability_id TEXT NOT NULL DEFAULT '',
                    intent TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'created',
                    pause_reason TEXT NOT NULL DEFAULT '',
                    retry_of TEXT NOT NULL DEFAULT '',
                    launch_id TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_runs_thread_state ON runs(thread_id, state, updated_at DESC);
                CREATE TABLE IF NOT EXISTS run_steps (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    step_index INTEGER NOT NULL,
                    tool TEXT NOT NULL,
                    effect_kind TEXT NOT NULL DEFAULT 'none',
                    canonical_args_hash TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'planned',
                    attempt INTEGER NOT NULL DEFAULT 1,
                    superseded_by TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id, step_index, attempt);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    step_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_checkpoints_run ON checkpoints(run_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS approval_challenges (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    step_id TEXT NOT NULL DEFAULT '',
                    thread_id TEXT NOT NULL DEFAULT '',
                    tool TEXT NOT NULL,
                    effect_kind TEXT NOT NULL DEFAULT 'none',
                    args_hash TEXT NOT NULL,
                    scope_hash TEXT NOT NULL DEFAULT '',
                    nonce TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    decision TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    resolved_at REAL,
                    consumed_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_challenges_run_status ON approval_challenges(run_id, status);
                CREATE TABLE IF NOT EXISTS effect_attempts (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    step_id TEXT NOT NULL DEFAULT '',
                    approval_id TEXT NOT NULL DEFAULT '',
                    effect_kind TEXT NOT NULL DEFAULT 'none',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_expires_at REAL NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'leased',
                    receipt_id TEXT NOT NULL DEFAULT '',
                    started_at REAL NOT NULL,
                    completed_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_effects_run ON effect_attempts(run_id, state);
                CREATE TABLE IF NOT EXISTS audit_entries (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL DEFAULT '',
                    step_id TEXT NOT NULL DEFAULT '',
                    effect_id TEXT NOT NULL DEFAULT '',
                    approval_id TEXT NOT NULL DEFAULT '',
                    receipt_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    redacted_payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_entries(run_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS resource_leases (
                    resource TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL DEFAULT '',
                    thread_id TEXT NOT NULL DEFAULT '',
                    owner TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1,
                    acquired_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rpc_dedup (
                    method TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    result_digest TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (method, request_key)
                );
                CREATE TABLE IF NOT EXISTS deletion_jobs (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'pending',
                    progress_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    completed_at REAL
                );
                """
            )

    # ---------------------------------------------------------------- runs

    def create_run(
        self,
        project_id: str,
        thread_id: str,
        intent: str = "",
        *,
        character_id: str = "",
        session_id: str = "",
        capability_id: str = "",
        launch_id: str = "",
        retry_of: str = "",
    ) -> dict[str, Any]:
        """Start a run, refusing a second active one on the same thread.

        Serialising per thread is what keeps two runs from interleaving
        approvals and events in one conversation (TDD §6.6).
        """
        now = time.time()
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connection:
            existing = self._connection.execute(
                f"SELECT id FROM runs WHERE thread_id=? AND state IN ({_placeholders(ACTIVE_RUN_STATES)}) LIMIT 1",
                (thread_id, *sorted(ACTIVE_RUN_STATES)),
            ).fetchone()
            if existing:
                return {"ok": False, "error": "thread_run_active", "active_run_id": str(existing["id"])}
            self._connection.execute(
                """INSERT INTO runs(
                    id,project_id,thread_id,character_id,session_id,capability_id,intent,state,retry_of,launch_id,revision,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, project_id, thread_id, character_id, session_id, capability_id, str(intent or "")[:2000], "created", retry_of, launch_id, 1, now, now),
            )
        return {"ok": True, "run": self.get_run(run_id), "context": self.run_context(run_id)}

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return _run_payload(row) if row else {}

    def run_context(self, run_id: str) -> RunContext | None:
        run = self.get_run(run_id)
        if not run:
            return None
        return RunContext(
            project_id=run["project_id"],
            thread_id=run["thread_id"],
            run_id=run["id"],
            character_id=run["character_id"],
            session_id=run["session_id"],
            capability_id=run["capability_id"],
        )

    def active_run(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                f"SELECT * FROM runs WHERE thread_id=? AND state IN ({_placeholders(ACTIVE_RUN_STATES)}) ORDER BY created_at DESC LIMIT 1",
                (thread_id, *sorted(ACTIVE_RUN_STATES)),
            ).fetchone()
        return _run_payload(row) if row else {}

    def transition_run(self, run_id: str, state: str, *, pause_reason: str = "", expected_revision: int | None = None) -> dict[str, Any]:
        if state not in RUN_STATES:
            return {"ok": False, "error": "invalid_run_state"}
        current = self.get_run(run_id)
        if not current:
            return {"ok": False, "error": "run_not_found"}
        if current["state"] in TERMINAL_RUN_STATES:
            # A finished run stays finished; retrying means a new run linked by
            # retry_of, so its history is never rewritten (TDD §8.1).
            return {"ok": False, "error": "run_already_terminal", "run": current}
        if expected_revision is not None and int(expected_revision) != int(current["revision"]):
            return {"ok": False, "error": "state_conflict", "run": current}
        now = time.time()
        completed_at = now if state in TERMINAL_RUN_STATES else None
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE runs SET state=?,pause_reason=?,revision=revision+1,updated_at=?,completed_at=COALESCE(?,completed_at) WHERE id=?",
                (state, pause_reason if state == "paused" else "", now, completed_at, run_id),
            )
        return {"ok": True, "run": self.get_run(run_id)}

    # --------------------------------------------------------------- steps

    def add_step(self, run_id: str, step_index: int, tool: str, *, effect_kind: str = "none", canonical_args_hash: str = "", attempt: int = 1) -> dict[str, Any]:
        now = time.time()
        step_id = f"step-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO run_steps(
                    id,run_id,step_index,tool,effect_kind,canonical_args_hash,state,attempt,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (step_id, run_id, int(step_index), tool, effect_kind, canonical_args_hash, "planned", max(1, int(attempt)), now, now),
            )
        return self.get_step(step_id)

    def get_step(self, step_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM run_steps WHERE id=?", (step_id,)).fetchone()
        return _step_payload(row) if row else {}

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM run_steps WHERE run_id=? ORDER BY step_index, attempt", (run_id,)).fetchall()
        return [_step_payload(row) for row in rows]

    def set_step_state(self, step_id: str, state: str, *, superseded_by: str = "") -> dict[str, Any]:
        if state not in STEP_STATES:
            return {"ok": False, "error": "invalid_step_state"}
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE run_steps SET state=?,superseded_by=?,updated_at=? WHERE id=?",
                (state, superseded_by, time.time(), step_id),
            )
        if not cursor.rowcount:
            return {"ok": False, "error": "step_not_found"}
        return {"ok": True, "step": self.get_step(step_id)}

    # --------------------------------------------------------- checkpoints

    def save_checkpoint(self, run_id: str, kind: str, state: dict[str, Any] | None = None, *, step_id: str = "") -> dict[str, Any]:
        checkpoint_id = f"ckpt-{uuid.uuid4().hex[:12]}"
        run = self.get_run(run_id)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO checkpoints(id,run_id,step_id,kind,state_json,revision,created_at) VALUES(?,?,?,?,?,?,?)",
                (checkpoint_id, run_id, step_id, kind, _json(state or {}), int(run.get("revision") or 1), time.time()),
            )
        return {"ok": True, "checkpoint_id": checkpoint_id}

    def latest_checkpoint(self, run_id: str, kind: str = "") -> dict[str, Any]:
        sql = "SELECT * FROM checkpoints WHERE run_id=?"
        values: list[Any] = [run_id]
        if kind:
            sql += " AND kind=?"
            values.append(kind)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        with self._lock:
            row = self._connection.execute(sql, tuple(values)).fetchone()
        if not row:
            return {}
        return {"id": row["id"], "run_id": row["run_id"], "step_id": row["step_id"], "kind": row["kind"], "state": _object(row["state_json"]), "revision": row["revision"], "created_at": row["created_at"]}

    # ---------------------------------------------------------- approvals

    def create_challenge(
        self,
        run_id: str,
        tool: str,
        args_hash: str,
        *,
        step_id: str = "",
        thread_id: str = "",
        effect_kind: str = "none",
        scope_hash: str = "",
        payload: dict[str, Any] | None = None,
        ttl_seconds: float = DEFAULT_APPROVAL_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Persist the approval before any side effect can happen."""
        now = time.time()
        challenge_id = f"challenge-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO approval_challenges(
                    id,run_id,step_id,thread_id,tool,effect_kind,args_hash,scope_hash,nonce,status,payload_json,revision,created_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (challenge_id, run_id, step_id, thread_id, tool, effect_kind, args_hash, scope_hash, uuid.uuid4().hex, "pending", _json(payload or {}), 1, now, now + max(1.0, float(ttl_seconds))),
            )
        return {"ok": True, "challenge": self.get_challenge(challenge_id)}

    def get_challenge(self, challenge_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM approval_challenges WHERE id=?", (challenge_id,)).fetchone()
        return _challenge_payload(row) if row else {}

    def list_pending_challenges(self, run_id: str = "", thread_id: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM approval_challenges WHERE status='pending'"
        values: list[Any] = []
        if run_id:
            sql += " AND run_id=?"
            values.append(run_id)
        if thread_id:
            sql += " AND thread_id=?"
            values.append(thread_id)
        sql += " ORDER BY created_at"
        with self._lock:
            rows = self._connection.execute(sql, tuple(values)).fetchall()
        return [_challenge_payload(row) for row in rows]

    def resolve_challenge(self, challenge_id: str, decision: str) -> dict[str, Any]:
        """Record approve/reject. Approval alone does not permit the action."""
        if decision not in {"approve", "reject", "cancel"}:
            return {"ok": False, "error": "invalid_decision"}
        challenge = self.get_challenge(challenge_id)
        if not challenge:
            return {"ok": False, "error": "challenge_not_found"}
        if challenge["status"] != "pending":
            return {"ok": False, "error": "challenge_not_pending", "challenge": challenge}
        if challenge["expires_at"] <= time.time():
            self._set_challenge_status(challenge_id, "expired")
            return {"ok": False, "error": "challenge_expired", "challenge": self.get_challenge(challenge_id)}
        status = {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[decision]
        self._set_challenge_status(challenge_id, status, decision=decision)
        return {"ok": True, "challenge": self.get_challenge(challenge_id)}

    def consume_challenge(self, challenge_id: str, *, tool: str, args_hash: str, scope_hash: str = "", thread_id: str = "") -> dict[str, Any]:
        """Spend an approval exactly once, re-verifying what it was granted for.

        Called immediately before the effect. Everything is rechecked here
        because time passed since the user decided: the plan may have been
        edited, the scope revoked, or the app restarted (TDD §7.3).
        """
        now = time.time()
        with self._lock, self._connection:
            row = self._connection.execute("SELECT * FROM approval_challenges WHERE id=?", (challenge_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "challenge_not_found"}
            challenge = _challenge_payload(row)
            if challenge["status"] == "consumed":
                # Distinguish "already spent" from "never approved": only the
                # former means an effect may already have happened.
                return {"ok": False, "error": "challenge_already_consumed", "challenge": challenge}
            if challenge["status"] != "approved":
                return {"ok": False, "error": "challenge_not_approved", "challenge": challenge}
            if challenge["expires_at"] <= now:
                self._connection.execute("UPDATE approval_challenges SET status='expired',revision=revision+1 WHERE id=?", (challenge_id,))
                return {"ok": False, "error": "challenge_expired", "challenge": self.get_challenge(challenge_id)}
            if challenge["tool"] != tool or challenge["args_hash"] != args_hash:
                # The step changed after approval; the old decision does not
                # transfer to it.
                self._connection.execute("UPDATE approval_challenges SET status='superseded',revision=revision+1 WHERE id=?", (challenge_id,))
                return {"ok": False, "error": "fingerprint_mismatch", "challenge": self.get_challenge(challenge_id)}
            if challenge["scope_hash"] and scope_hash and challenge["scope_hash"] != scope_hash:
                self._connection.execute("UPDATE approval_challenges SET status='superseded',revision=revision+1 WHERE id=?", (challenge_id,))
                return {"ok": False, "error": "scope_changed", "challenge": self.get_challenge(challenge_id)}
            if thread_id and challenge["thread_id"] and challenge["thread_id"] != thread_id:
                return {"ok": False, "error": "thread_mismatch", "challenge": challenge}
            # Single-use: the UPDATE only matches while the row is still
            # 'approved', so a concurrent second consumer gets rowcount 0.
            cursor = self._connection.execute(
                "UPDATE approval_challenges SET status='consumed',consumed_at=?,revision=revision+1 WHERE id=? AND status='approved'",
                (now, challenge_id),
            )
            if not cursor.rowcount:
                return {"ok": False, "error": "challenge_already_consumed", "challenge": self.get_challenge(challenge_id)}
        return {"ok": True, "challenge": self.get_challenge(challenge_id)}

    def supersede_challenge(self, challenge_id: str) -> dict[str, Any]:
        """Used when the user edits a step: the old decision never mutates."""
        challenge = self.get_challenge(challenge_id)
        if not challenge:
            return {"ok": False, "error": "challenge_not_found"}
        self._set_challenge_status(challenge_id, "superseded")
        return {"ok": True, "challenge": self.get_challenge(challenge_id)}

    def expire_stale_challenges(self, now: float | None = None) -> int:
        moment = time.time() if now is None else float(now)
        with self._lock, self._connection:
            return max(0, self._connection.execute(
                "UPDATE approval_challenges SET status='expired',revision=revision+1 WHERE status IN ('pending','approved') AND expires_at<=?",
                (moment,),
            ).rowcount)

    def _set_challenge_status(self, challenge_id: str, status: str, decision: str = "") -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE approval_challenges SET status=?,decision=?,resolved_at=?,revision=revision+1 WHERE id=?",
                (status, decision, time.time(), challenge_id),
            )

    # ------------------------------------------------------------- effects

    def acquire_effect_lease(
        self,
        run_id: str,
        idempotency_key: str,
        *,
        step_id: str = "",
        approval_id: str = "",
        effect_kind: str = "none",
        owner: str = "",
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Compare-and-swap the right to perform one external effect.

        The idempotency key is UNIQUE, so a duplicate resume loses the insert
        and is told what the original attempt is doing rather than acting again.
        """
        now = time.time()
        effect_id = f"effect-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connection:
            existing = self._connection.execute("SELECT * FROM effect_attempts WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing is not None:
                attempt = _effect_payload(existing)
                if attempt["state"] in {"observed", "verified"}:
                    return {"ok": False, "error": "effect_already_completed", "effect": attempt}
                if attempt["state"] == "acting":
                    # Someone started acting and never reported back. Only a
                    # human can say whether the outside world changed.
                    return {"ok": False, "error": "effect_needs_reconciliation", "effect": attempt}
                if attempt["state"] == "leased" and attempt["lease_expires_at"] > now:
                    return {"ok": False, "error": "effect_lease_held", "effect": attempt}
                # An expired lease or an abandoned/failed attempt may be retaken.
                self._connection.execute(
                    "UPDATE effect_attempts SET run_id=?,step_id=?,approval_id=?,lease_owner=?,lease_expires_at=?,state='leased',started_at=? WHERE id=?",
                    (run_id, step_id, approval_id, owner, now + max(1.0, float(ttl_seconds)), now, attempt["id"]),
                )
                return {"ok": True, "reclaimed": True, "effect": self.get_effect(attempt["id"])}
            self._connection.execute(
                """INSERT INTO effect_attempts(
                    id,run_id,step_id,approval_id,effect_kind,idempotency_key,lease_owner,lease_expires_at,state,started_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (effect_id, run_id, step_id, approval_id, effect_kind, idempotency_key, owner, now + max(1.0, float(ttl_seconds)), "leased", now),
            )
        return {"ok": True, "reclaimed": False, "effect": self.get_effect(effect_id)}

    def get_effect(self, effect_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM effect_attempts WHERE id=?", (effect_id,)).fetchone()
        return _effect_payload(row) if row else {}

    def effect_by_key(self, idempotency_key: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM effect_attempts WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        return _effect_payload(row) if row else {}

    def mark_effect(self, effect_id: str, state: str, *, receipt_id: str = "") -> dict[str, Any]:
        if state not in EFFECT_STATES:
            return {"ok": False, "error": "invalid_effect_state"}
        completed_at = time.time() if state in {"observed", "verified", "failed", "abandoned"} else None
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE effect_attempts SET state=?,receipt_id=?,completed_at=COALESCE(?,completed_at) WHERE id=?",
                (state, receipt_id, completed_at, effect_id),
            )
        if not cursor.rowcount:
            return {"ok": False, "error": "effect_not_found"}
        return {"ok": True, "effect": self.get_effect(effect_id)}

    def unreconciled_effects(self, run_id: str = "") -> list[dict[str, Any]]:
        """Effects that began but never produced a receipt.

        These are the crash cases: Joi may or may not have changed the outside
        world, so they are surfaced for a decision instead of being retried.
        """
        sql = "SELECT * FROM effect_attempts WHERE state IN ('leased','acting') AND receipt_id=''"
        values: list[Any] = []
        if run_id:
            sql += " AND run_id=?"
            values.append(run_id)
        sql += " ORDER BY started_at"
        with self._lock:
            rows = self._connection.execute(sql, tuple(values)).fetchall()
        return [_effect_payload(row) for row in rows]

    # --------------------------------------------------------------- audit

    def record_audit(self, kind: str, redacted_payload: dict[str, Any] | None = None, **links: str) -> dict[str, Any]:
        entry_id = f"audit-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO audit_entries(
                    id,run_id,step_id,effect_id,approval_id,receipt_id,kind,redacted_payload_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    entry_id,
                    str(links.get("run_id") or ""),
                    str(links.get("step_id") or ""),
                    str(links.get("effect_id") or ""),
                    str(links.get("approval_id") or ""),
                    str(links.get("receipt_id") or ""),
                    kind,
                    _json(redacted_payload or {}),
                    time.time(),
                ),
            )
        return {"ok": True, "audit_id": entry_id}

    def list_audit(self, run_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM audit_entries"
        values: list[Any] = []
        if run_id:
            sql += " WHERE run_id=?"
            values.append(run_id)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        values.append(max(1, min(int(limit), 500)))
        with self._lock:
            rows = self._connection.execute(sql, tuple(values)).fetchall()
        return [
            {"id": row["id"], "run_id": row["run_id"], "step_id": row["step_id"], "effect_id": row["effect_id"], "approval_id": row["approval_id"], "receipt_id": row["receipt_id"], "kind": row["kind"], "payload": _object(row["redacted_payload_json"]), "created_at": row["created_at"]}
            for row in rows
        ]

    # ------------------------------------------------------------- leases

    def acquire_resource(self, resource: str, *, run_id: str = "", thread_id: str = "", owner: str = "", ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS) -> dict[str, Any]:
        """Take a global OS resource. Concurrent threads still share one desktop."""
        now = time.time()
        with self._lock, self._connection:
            row = self._connection.execute("SELECT * FROM resource_leases WHERE resource=?", (resource,)).fetchone()
            if row is not None and float(row["expires_at"]) > now and str(row["run_id"]) != run_id:
                return {"ok": False, "error": "resource_busy", "lease": _lease_payload(row)}
            self._connection.execute(
                "INSERT INTO resource_leases(resource,run_id,thread_id,owner,revision,acquired_at,expires_at) VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(resource) DO UPDATE SET run_id=excluded.run_id,thread_id=excluded.thread_id,owner=excluded.owner,revision=resource_leases.revision+1,acquired_at=excluded.acquired_at,expires_at=excluded.expires_at",
                (resource, run_id, thread_id, owner, 1, now, now + max(1.0, float(ttl_seconds))),
            )
            updated = self._connection.execute("SELECT * FROM resource_leases WHERE resource=?", (resource,)).fetchone()
        return {"ok": True, "lease": _lease_payload(updated)}

    def release_resource(self, resource: str, *, run_id: str = "") -> dict[str, Any]:
        with self._lock, self._connection:
            if run_id:
                cursor = self._connection.execute("DELETE FROM resource_leases WHERE resource=? AND run_id=?", (resource, run_id))
            else:
                cursor = self._connection.execute("DELETE FROM resource_leases WHERE resource=?", (resource,))
        return {"ok": bool(cursor.rowcount)}

    # --------------------------------------------------------------- dedup

    def remember_rpc(self, method: str, request_key: str, result: dict[str, Any], ttl_seconds: float = 900.0) -> dict[str, Any]:
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO rpc_dedup(method,request_key,result_json,result_digest,created_at,expires_at) VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(method,request_key) DO NOTHING",
                (method, request_key, _json(result), canonical_digest(result), now, now + max(1.0, float(ttl_seconds))),
            )
        return self.recall_rpc(method, request_key)

    def recall_rpc(self, method: str, request_key: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM rpc_dedup WHERE method=? AND request_key=? AND expires_at>?",
                (method, request_key, time.time()),
            ).fetchone()
        return {"found": True, "result": _object(row["result_json"]), "digest": row["result_digest"]} if row else {"found": False}

    # ------------------------------------------------------------ recovery

    def reconcile_launch(self, launch_id: str) -> dict[str, Any]:
        """Settle runs, approvals and leases left behind by a dead process."""
        now = time.time()
        with self._lock, self._connection:
            runs = self._connection.execute(
                "UPDATE runs SET state='paused',pause_reason='recovery_required',revision=revision+1,updated_at=? WHERE state IN ('created','running')",
                (now,),
            ).rowcount
            # Approvals granted to a Joi that is gone must be asked again; an
            # 'approved' row surviving a restart would otherwise be spendable.
            challenges = self._connection.execute(
                "UPDATE approval_challenges SET status='expired',revision=revision+1 WHERE status='approved'",
                (),
            ).rowcount
            leases = self._connection.execute("DELETE FROM resource_leases", ()).rowcount
            abandoned = self._connection.execute(
                "UPDATE effect_attempts SET state='abandoned',completed_at=? WHERE state='leased' AND receipt_id=''",
                (now,),
            ).rowcount
        return {
            "runs_recovery_required": max(0, runs),
            "approvals_expired": max(0, challenges),
            "leases_cleared": max(0, leases),
            "effects_abandoned": max(0, abandoned),
            "needs_reconciliation": self.unreconciled_effects(),
        }


def _placeholders(values: Iterable[Any]) -> str:
    return ",".join("?" for _ in values)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "{}"


def _object(raw: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "thread_id": row["thread_id"],
        "character_id": row["character_id"],
        "session_id": row["session_id"],
        "capability_id": row["capability_id"],
        "intent": row["intent"],
        "state": row["state"],
        "pause_reason": row["pause_reason"],
        "recovery_required": str(row["pause_reason"] or "") == "recovery_required",
        "retry_of": row["retry_of"],
        "launch_id": row["launch_id"],
        "revision": row["revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _step_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "step_index": row["step_index"],
        "tool": row["tool"],
        "effect_kind": row["effect_kind"],
        "canonical_args_hash": row["canonical_args_hash"],
        "state": row["state"],
        "attempt": row["attempt"],
        "superseded_by": row["superseded_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _challenge_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "step_id": row["step_id"],
        "thread_id": row["thread_id"],
        "tool": row["tool"],
        "effect_kind": row["effect_kind"],
        "args_hash": row["args_hash"],
        "scope_hash": row["scope_hash"],
        "status": row["status"],
        "decision": row["decision"],
        "payload": _object(row["payload_json"]),
        "revision": row["revision"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "resolved_at": row["resolved_at"],
        "consumed_at": row["consumed_at"],
    }


def _effect_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "step_id": row["step_id"],
        "approval_id": row["approval_id"],
        "effect_kind": row["effect_kind"],
        "idempotency_key": row["idempotency_key"],
        "lease_owner": row["lease_owner"],
        "lease_expires_at": row["lease_expires_at"],
        "state": row["state"],
        "receipt_id": row["receipt_id"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }


def _lease_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "resource": row["resource"],
        "run_id": row["run_id"],
        "thread_id": row["thread_id"],
        "owner": row["owner"],
        "revision": row["revision"],
        "acquired_at": row["acquired_at"],
        "expires_at": row["expires_at"],
    }
