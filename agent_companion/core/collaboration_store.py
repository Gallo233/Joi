from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import threading
import time
from typing import Any, Iterable
import uuid

from agent_companion.core.action_intent import EffectKind, RED_LINE_EFFECTS, marker_effect
from agent_companion.core.run_store import RunStore


COLLABORATION_SCHEMA_VERSION = 2
DEFAULT_PROJECT_ID = "project-default"
DEFAULT_THREAD_ID = "thread-legacy"
PERMISSION_PROFILES = {"observe", "collaborate", "delegate"}

# Identifies this Joi process.  ``delegate`` trades the project-binding limit for
# a lifetime limit: it keeps acting outside the bound scope, but only for as long
# as the Joi the user granted it to is still running.  Grants restored from
# SQLite after a restart carry a stale launch id and fall back to confirmation.
LAUNCH_ID = f"launch-{uuid.uuid4().hex[:16]}"
SESSION_STATES = {"created", "running", "paused", "waiting_approval", "completed", "failed", "cancelled"}

# Pause reason for a session whose owning Joi process ended before it finished.
# It is a reason rather than a state so the public phase projection stays the
# small frozen vocabulary the shell already renders (TDD 8.1/8.2).
RECOVERY_REQUIRED = "recovery_required"
SENSITIVE_ACTIONS = {effect.value for effect in RED_LINE_EFFECTS}


def _red_line_name(effect: Any) -> str:
    """Normalize a typed or inferred effect to a red-line name, else ""."""
    value = effect.value if isinstance(effect, EffectKind) else str(effect or "")
    return value if value in SENSITIVE_ACTIONS else ""


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    default_character_id: str
    archived: bool
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class ConversationThread:
    id: str
    project_id: str
    title: str
    character_id: str
    archived: bool
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class ResourceBinding:
    id: str
    project_id: str
    kind: str
    value: str
    label: str
    metadata: dict[str, Any]
    created_at: float


@dataclass(frozen=True)
class CapabilitySession:
    id: str
    project_id: str
    thread_id: str
    capability: str
    goal: str
    permission_profile: str
    state: str
    driver: str
    budget: dict[str, Any]
    stop_conditions: list[str]
    created_at: float
    updated_at: float
    completed_at: float | None


def resolve_joi_data_home(workspace: Path) -> Path:
    configured = str(os.environ.get("JOI_DATA_HOME") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # Tests and source-only launches stay self-contained. The Tauri host passes
    # JOI_DATA_HOME so installed builds use the OS application data directory.
    return (workspace.resolve() / "data" / "agent_companion").resolve()


class CollaborationStore:
    """Durable project, thread, capability, permission and skill state.

    The legacy JSONL event stream remains the append-only compatibility log.
    This store imports it once, then receives every new event through EventBus.
    """

    def __init__(self, workspace: Path, data_home: Path | None = None, default_character_id: str = "builtin-hikari") -> None:
        self.workspace = workspace.resolve()
        self.data_home = (data_home or resolve_joi_data_home(self.workspace)).resolve()
        self.data_home.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_home / "joi.sqlite3"
        self.skill_root = self.data_home / "skills"
        self.skill_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()
        self._ensure_columns()
        self._ensure_defaults(default_character_id)
        self._migrate_legacy_events()
        # Schema v3 lives in its own module but the same database: its rows
        # reference projects/threads and must share transactions with them.
        self.runs = RunStore(self._connection, self._lock)
        self.launch_reconciliation = self._reconcile_previous_launch()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _create_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    default_character_id TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_threads (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    character_id TEXT NOT NULL DEFAULT '',
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_threads_project_updated
                    ON conversation_threads(project_id, archived, updated_at DESC);
                CREATE TABLE IF NOT EXISTS resource_bindings (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    value TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    UNIQUE(project_id, kind, value)
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    sequence INTEGER NOT NULL DEFAULT 0,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    thread_id TEXT NOT NULL REFERENCES conversation_threads(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL DEFAULT '',
                    character_id TEXT NOT NULL DEFAULT '',
                    public_phase TEXT NOT NULL DEFAULT '',
                    task_id TEXT NOT NULL DEFAULT '',
                    type TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_thread_sequence
                    ON events(thread_id, sequence, created_at);
                CREATE TABLE IF NOT EXISTS capability_sessions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    thread_id TEXT NOT NULL REFERENCES conversation_threads(id) ON DELETE CASCADE,
                    capability TEXT NOT NULL,
                    goal TEXT NOT NULL DEFAULT '',
                    permission_profile TEXT NOT NULL,
                    state TEXT NOT NULL,
                    pause_reason TEXT NOT NULL DEFAULT '',
                    driver TEXT NOT NULL DEFAULT 'native',
                    budget_json TEXT NOT NULL DEFAULT '{}',
                    stop_conditions_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_capability_thread_updated
                    ON capability_sessions(thread_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS permission_grants (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES capability_sessions(id) ON DELETE CASCADE,
                    profile TEXT NOT NULL,
                    scope_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    status_reason TEXT NOT NULL DEFAULT '',
                    launch_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    expires_at REAL
                );
                CREATE TABLE IF NOT EXISTS action_receipts (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES capability_sessions(id) ON DELETE CASCADE,
                    step_index INTEGER NOT NULL DEFAULT 0,
                    action TEXT NOT NULL,
                    risk TEXT NOT NULL DEFAULT 'medium',
                    before_summary TEXT NOT NULL DEFAULT '',
                    after_summary TEXT NOT NULL DEFAULT '',
                    verification_json TEXT NOT NULL DEFAULT '{}',
                    duration_ms REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'unknown',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_receipts_session_step
                    ON action_receipts(session_id, step_index);
                CREATE TABLE IF NOT EXISTS skill_installations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL DEFAULT '0.0.0',
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    root_path TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(name, scope, scope_id)
                );
                CREATE TABLE IF NOT EXISTS skill_runs (
                    id TEXT PRIMARY KEY,
                    installation_id TEXT NOT NULL REFERENCES skill_installations(id) ON DELETE CASCADE,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    thread_id TEXT NOT NULL REFERENCES conversation_threads(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    completed_at REAL
                );
                CREATE TABLE IF NOT EXISTS skill_drafts (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    thread_id TEXT NOT NULL REFERENCES conversation_threads(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            self._set_meta("schema_version", str(COLLABORATION_SCHEMA_VERSION))

    def _ensure_columns(self) -> None:
        """Add columns introduced after a database was first created.

        ``CREATE TABLE IF NOT EXISTS`` silently keeps an older table's shape, so
        every column added later needs an explicit backfill here.
        """
        added: dict[str, tuple[tuple[str, str], ...]] = {
            "permission_grants": (("launch_id", "TEXT NOT NULL DEFAULT ''"), ("status_reason", "TEXT NOT NULL DEFAULT ''")),
            "events": (("public_phase", "TEXT NOT NULL DEFAULT ''"),),
            "capability_sessions": (("pause_reason", "TEXT NOT NULL DEFAULT ''"),),
        }
        with self._lock, self._connection:
            for table, columns in added.items():
                existing = {str(row["name"]) for row in self._connection.execute(f"PRAGMA table_info({table})")}
                if not existing:
                    continue
                for name, definition in columns:
                    if name not in existing:
                        self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _reconcile_previous_launch(self) -> dict[str, Any]:
        """Settle whatever the previous Joi process left mid-flight.

        A row saying ``running`` only ever meant "running in the process that
        wrote it". After a crash or quit that process is gone, so the session
        cannot be shown as executing and must not resume on its own: it becomes
        ``paused`` with ``recovery_required`` and waits for the user.

        ``waiting_approval`` is deliberately left alone -- it is already a state
        that blocks on a person, and downgrading it would discard the pending
        decision. Delegate grants from an ended launch are expired here so no
        later code path can find an active one (see LAUNCH_ID).
        """
        now = time.time()
        with self._lock, self._connection:
            expired = self._connection.execute(
                "UPDATE permission_grants SET status='expired',status_reason='launch_ended' WHERE status='active' AND profile='delegate' AND launch_id<>?",
                (LAUNCH_ID,),
            ).rowcount
            recovered = self._connection.execute(
                "UPDATE capability_sessions SET state='paused',pause_reason=?,updated_at=? WHERE state='running'",
                (RECOVERY_REQUIRED, now),
            ).rowcount
        return {
            "expired_delegate_grants": max(0, expired),
            "sessions_recovery_required": max(0, recovered),
            **self.runs.reconcile_launch(LAUNCH_ID),
        }

    def _ensure_defaults(self, character_id: str) -> None:
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO projects(id,name,default_character_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                (DEFAULT_PROJECT_ID, "默认项目", character_id or "builtin-hikari", now, now),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO conversation_threads(id,project_id,title,character_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (DEFAULT_THREAD_ID, DEFAULT_PROJECT_ID, "原有对话", character_id or "builtin-hikari", now, now),
            )
            self._set_meta("active_project_id", self._get_meta("active_project_id") or DEFAULT_PROJECT_ID)
            self._set_meta("active_thread_id", self._get_meta("active_thread_id") or DEFAULT_THREAD_ID)

    def context(self) -> dict[str, str]:
        with self._lock:
            project_id = self._get_meta("active_project_id") or DEFAULT_PROJECT_ID
            thread_id = self._get_meta("active_thread_id") or DEFAULT_THREAD_ID
            thread = self.get_thread(thread_id)
            project = self.get_project(project_id)
            character_id = ""
            if thread:
                character_id = thread.character_id
            if not character_id and project:
                character_id = project.default_character_id
            session_id, session_state = self._active_session(thread_id)
            return {
                "project_id": project_id,
                "thread_id": thread_id,
                "session_id": session_id,
                "session_state": session_state,
                "character_id": character_id or "builtin-hikari",
            }

    def snapshot(self) -> dict[str, Any]:
        context = self.context()
        return {
            "schema_version": COLLABORATION_SCHEMA_VERSION,
            "active": context,
            "projects": self.list_projects(),
            "threads": self.list_threads(context["project_id"]),
            "bindings": self.list_bindings(context["project_id"]),
            "capability_session": self.latest_session(context["thread_id"]),
        }

    def list_projects(self, include_archived: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM projects"
        values: tuple[Any, ...] = ()
        if not include_archived:
            query += " WHERE archived=0"
        query += " ORDER BY updated_at DESC, name COLLATE NOCASE"
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
        return [self._project_from_row(row) for row in rows]

    def get_project(self, project_id: str) -> Project | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return Project(**self._project_from_row(row)) if row else None

    def create_project(self, name: str, default_character_id: str = "") -> dict[str, Any]:
        clean_name = _clean_label(name, "新项目", 80)
        now = time.time()
        project_id = f"project-{uuid.uuid4().hex[:12]}"
        thread_id = f"thread-{uuid.uuid4().hex[:12]}"
        character_id = default_character_id or self.context().get("character_id") or "builtin-hikari"
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO projects(id,name,default_character_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                (project_id, clean_name, character_id, now, now),
            )
            self._connection.execute(
                "INSERT INTO conversation_threads(id,project_id,title,character_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (thread_id, project_id, "新对话", character_id, now, now),
            )
        self.activate_thread(thread_id)
        return {"project": asdict(self.get_project(project_id)), "thread": self.thread_payload(thread_id)}

    def update_project(self, project_id: str, *, name: str | None = None, default_character_id: str | None = None, archived: bool | None = None) -> dict[str, Any]:
        project = self.get_project(project_id)
        if project is None:
            return {"ok": False, "error": "project_not_found"}
        values = {
            "name": _clean_label(name, project.name, 80) if name is not None else project.name,
            "default_character_id": _clean_identifier(default_character_id) if default_character_id is not None else project.default_character_id,
            "archived": int(bool(archived)) if archived is not None else int(project.archived),
            "updated_at": time.time(),
        }
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE projects SET name=?,default_character_id=?,archived=?,updated_at=? WHERE id=?",
                (values["name"], values["default_character_id"], values["archived"], values["updated_at"], project_id),
            )
        return {"ok": True, "project": asdict(self.get_project(project_id))}

    def delete_project(self, project_id: str, confirmed: bool = False) -> dict[str, Any]:
        if project_id == DEFAULT_PROJECT_ID:
            return {"ok": False, "error": "default_project_protected"}
        project = self.get_project(project_id)
        if project is None:
            return {"ok": False, "error": "project_not_found"}
        if not confirmed or not project.archived:
            return {"ok": False, "error": "archive_and_confirm_required"}
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM projects WHERE id=?", (project_id,))
        if self.context()["project_id"] == project_id:
            self.activate_thread(DEFAULT_THREAD_ID)
        return {"ok": True, "deleted_id": project_id}

    def list_threads(self, project_id: str, include_archived: bool = False, query: str = "") -> list[dict[str, Any]]:
        clauses = ["project_id=?"]
        values: list[Any] = [project_id]
        if not include_archived:
            clauses.append("archived=0")
        if query.strip():
            clauses.append("title LIKE ?")
            values.append(f"%{query.strip()[:80]}%")
        sql = "SELECT * FROM conversation_threads WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC"
        with self._lock:
            rows = self._connection.execute(sql, tuple(values)).fetchall()
        return [self._thread_from_row(row) for row in rows]

    def get_thread(self, thread_id: str) -> ConversationThread | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM conversation_threads WHERE id=?", (thread_id,)).fetchone()
        return ConversationThread(**self._thread_from_row(row)) if row else None

    def thread_payload(self, thread_id: str) -> dict[str, Any]:
        thread = self.get_thread(thread_id)
        return asdict(thread) if thread else {}

    def create_thread(self, project_id: str, title: str = "", character_id: str = "") -> dict[str, Any]:
        project = self.get_project(project_id)
        if project is None:
            return {"ok": False, "error": "project_not_found"}
        now = time.time()
        thread_id = f"thread-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO conversation_threads(id,project_id,title,character_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (thread_id, project_id, _clean_label(title, "新对话", 100), character_id or project.default_character_id, now, now),
            )
        self.activate_thread(thread_id)
        return {"ok": True, "thread": self.thread_payload(thread_id)}

    def update_thread(self, thread_id: str, *, title: str | None = None, character_id: str | None = None, archived: bool | None = None) -> dict[str, Any]:
        thread = self.get_thread(thread_id)
        if thread is None:
            return {"ok": False, "error": "thread_not_found"}
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE conversation_threads SET title=?,character_id=?,archived=?,updated_at=? WHERE id=?",
                (
                    _clean_label(title, thread.title, 100) if title is not None else thread.title,
                    _clean_identifier(character_id) if character_id is not None else thread.character_id,
                    int(bool(archived)) if archived is not None else int(thread.archived),
                    time.time(),
                    thread_id,
                ),
            )
        return {"ok": True, "thread": self.thread_payload(thread_id)}

    def delete_thread(self, thread_id: str, confirmed: bool = False) -> dict[str, Any]:
        if thread_id == DEFAULT_THREAD_ID:
            return {"ok": False, "error": "default_thread_protected"}
        thread = self.get_thread(thread_id)
        if thread is None:
            return {"ok": False, "error": "thread_not_found"}
        if not confirmed or not thread.archived:
            return {"ok": False, "error": "archive_and_confirm_required"}
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM conversation_threads WHERE id=?", (thread_id,))
        if self.context()["thread_id"] == thread_id:
            fallback = self.list_threads(thread.project_id)
            self.activate_thread(str((fallback[0] if fallback else {"id": DEFAULT_THREAD_ID})["id"]))
        return {"ok": True, "deleted_id": thread_id}

    def activate_thread(self, thread_id: str) -> dict[str, Any]:
        thread = self.get_thread(thread_id)
        if thread is None or thread.archived:
            return {"ok": False, "error": "thread_not_found"}
        with self._lock, self._connection:
            self._set_meta("active_project_id", thread.project_id)
            self._set_meta("active_thread_id", thread.id)
            self._connection.execute("UPDATE conversation_threads SET updated_at=? WHERE id=?", (time.time(), thread.id))
        return {"ok": True, "active": self.context(), "thread": asdict(thread)}

    def add_binding(self, project_id: str, kind: str, value: str, label: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.get_project(project_id) is None:
            return {"ok": False, "error": "project_not_found"}
        clean_kind = kind.strip().casefold()
        if clean_kind not in {"directory", "application", "domain", "game"}:
            return {"ok": False, "error": "unsupported_binding_kind"}
        clean_value = str(value or "").strip()
        if not clean_value:
            return {"ok": False, "error": "missing_binding_value"}
        binding_id = f"binding-{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    "INSERT INTO resource_bindings(id,project_id,kind,value,label,metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (binding_id, project_id, clean_kind, clean_value, _clean_label(label, _binding_label(clean_kind, clean_value), 100), _json(metadata or {}), now),
                )
            except sqlite3.IntegrityError:
                return {"ok": False, "error": "binding_exists"}
        return {"ok": True, "binding": self.get_binding(binding_id)}

    def get_binding(self, binding_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM resource_bindings WHERE id=?", (binding_id,)).fetchone()
        return self._binding_from_row(row) if row else {}

    def list_bindings(self, project_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM resource_bindings WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()
        return [self._binding_from_row(row) for row in rows]

    def remove_binding(self, binding_id: str) -> dict[str, Any]:
        with self._lock, self._connection:
            cursor = self._connection.execute("DELETE FROM resource_bindings WHERE id=?", (binding_id,))
        return {"ok": bool(cursor.rowcount), "deleted_id": binding_id if cursor.rowcount else "", "error": "" if cursor.rowcount else "binding_not_found"}

    def record_event(self, event: Any) -> None:
        payload = event.to_dict() if hasattr(event, "to_dict") else dict(event or {})
        if not isinstance(payload, dict):
            return
        context = self.context()
        state = payload.get("agent_state") if isinstance(payload.get("agent_state"), dict) else {}
        for key in ("project_id", "thread_id", "session_id", "character_id", "public_phase"):
            payload[key] = str(payload.get(key) or state.get(key) or context.get(key) or "")
        event_id = str(payload.get("event_id") or _event_digest(payload))
        payload["event_id"] = event_id
        project_id = payload["project_id"] or DEFAULT_PROJECT_ID
        thread_id = payload["thread_id"] or DEFAULT_THREAD_ID
        if self.get_project(project_id) is None or self.get_thread(thread_id) is None:
            project_id, thread_id = DEFAULT_PROJECT_ID, DEFAULT_THREAD_ID
            payload["project_id"], payload["thread_id"] = project_id, thread_id
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT OR IGNORE INTO events(
                    event_id,sequence,project_id,thread_id,session_id,character_id,public_phase,task_id,type,created_at,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    _safe_int(payload.get("sequence")),
                    project_id,
                    thread_id,
                    payload.get("session_id") or "",
                    payload.get("character_id") or "",
                    payload.get("public_phase") or "",
                    str(payload.get("task_id") or ""),
                    str(payload.get("type") or "event"),
                    _safe_float(payload.get("created_at"), time.time()),
                    _json(payload),
                ),
            )
            self._connection.execute("UPDATE conversation_threads SET updated_at=? WHERE id=?", (time.time(), thread_id))

    def history(self, thread_id: str, limit: int = 160, after_sequence: int = 0) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 400))
        sequence_clause = " AND sequence>?" if after_sequence > 0 else ""
        values: list[Any] = [thread_id]
        if after_sequence > 0:
            values.append(int(after_sequence))
        values.append(safe_limit)
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM events WHERE thread_id=?" + sequence_clause + " ORDER BY sequence DESC,created_at DESC LIMIT ?",
                tuple(values),
            ).fetchall()
        payloads = [_object(row["payload_json"]) for row in reversed(rows)]
        return [row for row in payloads if row]

    def start_session(
        self,
        capability: str,
        goal: str,
        permission_profile: str = "observe",
        *,
        project_id: str = "",
        thread_id: str = "",
        driver: str = "native",
        budget: dict[str, Any] | None = None,
        stop_conditions: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        context = self.context()
        project_id = project_id or context["project_id"]
        thread_id = thread_id or context["thread_id"]
        profile = permission_profile if permission_profile in PERMISSION_PROFILES else "observe"
        if self.get_project(project_id) is None or self.get_thread(thread_id) is None:
            return {"ok": False, "error": "invalid_session_context"}
        now = time.time()
        session_id = f"session-{uuid.uuid4().hex[:12]}"
        safe_budget = _normalize_budget(budget)
        safe_stops = [str(item)[:120] for item in (stop_conditions or []) if str(item).strip()][:12]
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO capability_sessions(
                    id,project_id,thread_id,capability,goal,permission_profile,state,driver,budget_json,stop_conditions_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (session_id, project_id, thread_id, _clean_identifier(capability, "computer_use"), str(goal or "")[:2000], profile, "running", _clean_identifier(driver, "native"), _json(safe_budget), _json(safe_stops), now, now),
            )
            self._connection.execute(
                "INSERT INTO permission_grants(id,session_id,profile,scope_json,status,launch_id,created_at) VALUES(?,?,?,?,?,?,?)",
                (f"grant-{uuid.uuid4().hex[:12]}", session_id, profile, _json(self._project_scope(project_id)), "active", LAUNCH_ID, now),
            )
        return {"ok": True, "session": self.session_payload(session_id)}

    def session_payload(self, session_id: str, include_receipts: bool = True) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM capability_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            return {}
        payload = self._session_from_row(row)
        if include_receipts:
            payload["receipts"] = self.list_receipts(session_id, 50)
        payload["permission"] = self.permission_for_session(session_id)
        return payload

    def latest_session(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT id FROM capability_sessions WHERE thread_id=? ORDER BY updated_at DESC LIMIT 1", (thread_id,)).fetchone()
        return self.session_payload(str(row["id"])) if row else {}

    def transition_session(self, session_id: str, state: str, pause_reason: str = "") -> dict[str, Any]:
        clean_state = state if state in SESSION_STATES else "paused"
        completed_at = time.time() if clean_state in {"completed", "failed", "cancelled"} else None
        # The reason only describes a pause; leaving it set on resume would keep
        # a recovered session looking unrecovered.
        clean_reason = _clean_identifier(pause_reason)[:60] if clean_state == "paused" else ""
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE capability_sessions SET state=?,pause_reason=?,updated_at=?,completed_at=COALESCE(?,completed_at) WHERE id=?",
                (clean_state, clean_reason, time.time(), completed_at, session_id),
            )
        if not cursor.rowcount:
            return {"ok": False, "error": "session_not_found"}
        return {"ok": True, "session": self.session_payload(session_id)}

    def permission_for_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM permission_grants WHERE session_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if not row:
            return {}
        launch_id = str(row["launch_id"] or "")
        return {
            "id": row["id"],
            "profile": row["profile"],
            "scope": _object(row["scope_json"]),
            "status": row["status"],
            "launch_id": launch_id,
            "current_launch": launch_id == LAUNCH_ID,
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }

    def grant_permission(self, session_id: str, profile: str, scope: dict[str, Any] | None = None) -> dict[str, Any]:
        session = self.session_payload(session_id, include_receipts=False)
        if not session:
            return {"ok": False, "error": "session_not_found"}
        if profile not in PERMISSION_PROFILES:
            return {"ok": False, "error": "invalid_permission_profile"}
        now = time.time()
        grant_id = f"grant-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connection:
            self._connection.execute("UPDATE permission_grants SET status='revoked' WHERE session_id=? AND status='active'", (session_id,))
            self._connection.execute(
                "INSERT INTO permission_grants(id,session_id,profile,scope_json,status,launch_id,created_at) VALUES(?,?,?,?,?,?,?)",
                (grant_id, session_id, profile, _json(scope or self._project_scope(str(session.get("project_id") or ""))), "active", LAUNCH_ID, now),
            )
            self._connection.execute("UPDATE capability_sessions SET permission_profile=?,updated_at=? WHERE id=?", (profile, now, session_id))
        return {"ok": True, "permission": self.permission_for_session(session_id), "session": self.session_payload(session_id)}

    def expand_permission(self, session_id: str, scope: dict[str, Any] | None = None, *, confirmed: bool = False) -> dict[str, Any]:
        """Widen an existing grant's scope, never its profile.

        Expansion is its own step because it is the one permission change the
        user cannot infer from the profile they picked: the session keeps the
        档位 they chose while reaching somewhere new.  Unconfirmed calls return
        exactly what would be added so the UI can name it before anything moves.
        """
        permission = self.permission_for_session(session_id)
        if not permission:
            return {"ok": False, "error": "permission_not_found"}
        current = permission.get("scope") if isinstance(permission.get("scope"), dict) else {}
        additions = _scope_additions(current, scope or {})
        if not additions:
            return {"ok": True, "changed": False, "additions": {}, "permission": permission}
        if not confirmed:
            return {
                "ok": False,
                "error": "expand_confirmation_required",
                "requires_approval": True,
                "additions": additions,
                "permission": permission,
            }
        merged = _merged_scope(current, additions)
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE permission_grants SET scope_json=?,launch_id=? WHERE id=? AND status='active'",
                (_json(merged), LAUNCH_ID, permission["id"]),
            )
            self._connection.execute("UPDATE capability_sessions SET updated_at=? WHERE id=?", (now, session_id))
        return {
            "ok": True,
            "changed": True,
            "additions": additions,
            "permission": self.permission_for_session(session_id),
            "session": self.session_payload(session_id),
        }

    def revoke_permission(self, session_id: str) -> dict[str, Any]:
        with self._lock, self._connection:
            cursor = self._connection.execute("UPDATE permission_grants SET status='revoked' WHERE session_id=? AND status='active'", (session_id,))
            self._connection.execute("UPDATE capability_sessions SET state='paused',updated_at=? WHERE id=?", (time.time(), session_id))
        return {"ok": bool(cursor.rowcount), "session": self.session_payload(session_id)}

    def action_allowed(self, session_id: str, action: str, risk: str = "medium", *, signals: Iterable[Any] = (), effect_kind: str = "") -> dict[str, Any]:
        """Single gate for "may this session run this action unattended?".

        ``effect_kind`` is the typed classification derived from the tool's own
        schema and is authoritative. ``signals`` carries free text the caller
        knows about the action (planner reason, resolved target label, typed
        text, URL); it can only raise the verdict, never lower it, so a bare
        coordinate click onto a payment control is still caught while a tool
        already typed as a red line cannot be talked down. See TDD §9.
        """
        permission = self.permission_for_session(session_id)
        profile = str(permission.get("profile") or "observe")
        action_key = _clean_identifier(action)
        sensitive = _red_line_name(effect_kind) or _red_line_name(marker_effect(action, *signals))
        if sensitive:
            return {"allowed": False, "requires_approval": True, "reason": "sensitive_action", "sensitive_action": sensitive}
        if profile == "observe":
            return {"allowed": risk == "low" and action_key.startswith("observe"), "requires_approval": risk != "low" or not action_key.startswith("observe"), "reason": "observe_only"}
        if profile == "delegate" and not permission.get("current_launch"):
            # The user delegated to a Joi that has since exited; a restored grant
            # must not silently keep acting on their behalf.
            return {"allowed": False, "requires_approval": True, "reason": "delegate_launch_expired"}
        return {"allowed": True, "requires_approval": False, "reason": f"{profile}_session_grant", "scope_bound": profile != "delegate"}

    def add_receipt(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.session_payload(session_id, include_receipts=False):
            return {"ok": False, "error": "session_not_found"}
        receipt_id = f"receipt-{uuid.uuid4().hex[:12]}"
        with self._lock, self._connection:
            step_index = _safe_int(payload.get("step_index"))
            if step_index <= 0:
                row = self._connection.execute("SELECT COALESCE(MAX(step_index),0)+1 AS next_step FROM action_receipts WHERE session_id=?", (session_id,)).fetchone()
                step_index = int(row["next_step"] if row else 1)
            self._connection.execute(
                """INSERT INTO action_receipts(
                    id,session_id,step_index,action,risk,before_summary,after_summary,verification_json,duration_ms,status,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    receipt_id,
                    session_id,
                    step_index,
                    _clean_identifier(payload.get("action"), "unknown"),
                    _clean_identifier(payload.get("risk"), "medium"),
                    str(payload.get("before_summary") or "")[:500],
                    str(payload.get("after_summary") or "")[:500],
                    _json(payload.get("verification") if isinstance(payload.get("verification"), dict) else {}),
                    max(0.0, _safe_float(payload.get("duration_ms"), 0.0)),
                    _clean_identifier(payload.get("status"), "unknown"),
                    time.time(),
                ),
            )
            self._connection.execute("UPDATE capability_sessions SET updated_at=? WHERE id=?", (time.time(), session_id))
        return {"ok": True, "receipt": self.get_receipt(receipt_id)}

    def get_receipt(self, receipt_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM action_receipts WHERE id=?", (receipt_id,)).fetchone()
        return self._receipt_from_row(row) if row else {}

    def list_receipts(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM action_receipts WHERE session_id=? ORDER BY step_index LIMIT ?", (session_id, max(1, min(limit, 200)))).fetchall()
        return [self._receipt_from_row(row) for row in rows]

    def upsert_skill_installation(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        installation_id = str(payload.get("id") or f"skill-{uuid.uuid4().hex[:12]}")
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO skill_installations(
                    id,name,version,scope,scope_id,source,root_path,digest,manifest_json,enabled,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(name,scope,scope_id) DO UPDATE SET
                    version=excluded.version,source=excluded.source,root_path=excluded.root_path,digest=excluded.digest,
                    manifest_json=excluded.manifest_json,enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (
                    installation_id,
                    payload["name"],
                    payload.get("version") or "0.0.0",
                    payload.get("scope") or "global",
                    payload.get("scope_id") or "",
                    payload.get("source") or "",
                    payload["root_path"],
                    payload["digest"],
                    _json(payload.get("manifest") or {}),
                    int(payload.get("enabled", True)),
                    now,
                    now,
                ),
            )
        row = self.skill_installation_by_key(payload["name"], payload.get("scope") or "global", payload.get("scope_id") or "")
        return row

    def list_skill_installations(self, project_id: str = "", character_id: str = "", include_disabled: bool = True) -> list[dict[str, Any]]:
        clauses = ["(scope='global' OR (scope='project' AND scope_id=?) OR (scope='character' AND scope_id=?))"]
        values: list[Any] = [project_id, character_id]
        if not include_disabled:
            clauses.append("enabled=1")
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM skill_installations WHERE " + " AND ".join(clauses) + " ORDER BY CASE scope WHEN 'project' THEN 1 WHEN 'character' THEN 2 ELSE 3 END,name",
                tuple(values),
            ).fetchall()
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = self._skill_from_row(row)
            if payload["name"] in seen:
                continue
            seen.add(payload["name"])
            result.append(payload)
        return result

    def skill_installation(self, installation_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM skill_installations WHERE id=?", (installation_id,)).fetchone()
        return self._skill_from_row(row) if row else {}

    def skill_installation_by_key(self, name: str, scope: str, scope_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM skill_installations WHERE name=? AND scope=? AND scope_id=?", (name, scope, scope_id)).fetchone()
        return self._skill_from_row(row) if row else {}

    def set_skill_enabled(self, installation_id: str, enabled: bool) -> dict[str, Any]:
        with self._lock, self._connection:
            cursor = self._connection.execute("UPDATE skill_installations SET enabled=?,updated_at=? WHERE id=?", (int(enabled), time.time(), installation_id))
        return {"ok": bool(cursor.rowcount), "skill": self.skill_installation(installation_id)}

    def delete_skill_installation(self, installation_id: str) -> dict[str, Any]:
        row = self.skill_installation(installation_id)
        if not row:
            return {"ok": False, "error": "skill_not_found"}
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM skill_installations WHERE id=?", (installation_id,))
        return {"ok": True, "skill": row}

    def record_skill_run(self, installation_id: str, status: str, summary: str) -> dict[str, Any]:
        installation = self.skill_installation(installation_id)
        if not installation:
            return {"ok": False, "error": "skill_not_found"}
        context = self.context()
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO skill_runs(id,installation_id,project_id,thread_id,session_id,status,summary,created_at,completed_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    installation_id,
                    context.get("project_id") or DEFAULT_PROJECT_ID,
                    context.get("thread_id") or DEFAULT_THREAD_ID,
                    context.get("session_id") or "",
                    _clean_identifier(status, "failed"),
                    str(summary or "")[:20_000],
                    now,
                    now,
                ),
            )
        return {"ok": True, "run_id": run_id}

    def create_skill_draft(self, project_id: str, thread_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        draft_id = f"draft-{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO skill_drafts(id,project_id,thread_id,name,payload_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (draft_id, project_id, thread_id, _clean_label(name, "未命名技能", 64), _json(payload), "draft", now, now),
            )
        return {"id": draft_id, "project_id": project_id, "thread_id": thread_id, "name": _clean_label(name, "未命名技能", 64), "payload": payload, "status": "draft", "created_at": now, "updated_at": now}

    def list_skill_drafts(self, project_id: str, thread_id: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM skill_drafts WHERE project_id=?"
        values: list[Any] = [project_id]
        if thread_id:
            sql += " AND thread_id=?"
            values.append(thread_id)
        sql += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self._connection.execute(sql, tuple(values)).fetchall()
        return [
            {"id": row["id"], "project_id": row["project_id"], "thread_id": row["thread_id"], "name": row["name"], "payload": _object(row["payload_json"]), "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"]}
            for row in rows
        ]

    def get_skill_draft(self, draft_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM skill_drafts WHERE id=?", (draft_id,)).fetchone()
        if not row:
            return {}
        return {"id": row["id"], "project_id": row["project_id"], "thread_id": row["thread_id"], "name": row["name"], "payload": _object(row["payload_json"]), "status": row["status"], "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def update_skill_draft(self, draft_id: str, status: str) -> dict[str, Any]:
        safe_status = status if status in {"draft", "approved", "rejected", "installed"} else "draft"
        with self._lock, self._connection:
            cursor = self._connection.execute("UPDATE skill_drafts SET status=?,updated_at=? WHERE id=?", (safe_status, time.time(), draft_id))
            row = self._connection.execute("SELECT * FROM skill_drafts WHERE id=?", (draft_id,)).fetchone()
        if not cursor.rowcount or not row:
            return {"ok": False, "error": "draft_not_found"}
        return {"ok": True, "draft": {"id": row["id"], "name": row["name"], "status": row["status"], "payload": _object(row["payload_json"]), "updated_at": row["updated_at"]}}

    def _project_scope(self, project_id: str) -> dict[str, Any]:
        grouped: dict[str, list[str]] = {"directory": [], "application": [], "domain": [], "game": []}
        for binding in self.list_bindings(project_id):
            grouped.setdefault(binding["kind"], []).append(binding["value"])
        return grouped

    def _active_session(self, thread_id: str) -> tuple[str, str]:
        with self._lock:
            row = self._connection.execute(
                "SELECT id,state FROM capability_sessions WHERE thread_id=? AND state IN ('running','paused','waiting_approval') ORDER BY updated_at DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        return (str(row["id"]), str(row["state"])) if row else ("", "")

    def _active_session_id(self, thread_id: str) -> str:
        return self._active_session(thread_id)[0]

    def _migrate_legacy_events(self) -> None:
        if self._get_meta("legacy_events_migrated") == "1":
            return
        legacy_path = self.workspace / "data" / "agent_companion" / "events.jsonl"
        if not legacy_path.is_file():
            self._set_meta("legacy_events_migrated", "1")
            return
        backup = legacy_path.with_suffix(".jsonl.pre-sqlite-backup")
        if not backup.exists():
            try:
                shutil.copy2(legacy_path, backup)
            except OSError:
                pass
        try:
            with legacy_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(row, dict):
                        state = row.get("agent_state") if isinstance(row.get("agent_state"), dict) else {}
                        row.setdefault("project_id", DEFAULT_PROJECT_ID)
                        row.setdefault("thread_id", DEFAULT_THREAD_ID)
                        row.setdefault("character_id", state.get("character_id") or "builtin-hikari")
                        self.record_event(row)
        except OSError:
            return
        self._set_meta("legacy_events_migrated", "1")

    def _get_meta(self, key: str) -> str:
        row = self._connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    def _set_meta(self, key: str, value: str) -> None:
        self._connection.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "name": row["name"], "default_character_id": row["default_character_id"], "archived": bool(row["archived"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}

    @staticmethod
    def _thread_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "project_id": row["project_id"], "title": row["title"], "character_id": row["character_id"], "archived": bool(row["archived"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}

    @staticmethod
    def _binding_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "project_id": row["project_id"], "kind": row["kind"], "value": row["value"], "label": row["label"], "metadata": _object(row["metadata_json"]), "created_at": row["created_at"]}

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> dict[str, Any]:
        pause_reason = str(row["pause_reason"] or "")
        return {"id": row["id"], "project_id": row["project_id"], "thread_id": row["thread_id"], "capability": row["capability"], "goal": row["goal"], "permission_profile": row["permission_profile"], "state": row["state"], "pause_reason": pause_reason, "recovery_required": pause_reason == RECOVERY_REQUIRED, "driver": row["driver"], "budget": _object(row["budget_json"]), "stop_conditions": _array(row["stop_conditions_json"]), "created_at": row["created_at"], "updated_at": row["updated_at"], "completed_at": row["completed_at"]}

    @staticmethod
    def _receipt_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "session_id": row["session_id"], "step_index": row["step_index"], "action": row["action"], "risk": row["risk"], "before_summary": row["before_summary"], "after_summary": row["after_summary"], "verification": _object(row["verification_json"]), "duration_ms": row["duration_ms"], "status": row["status"], "created_at": row["created_at"]}

    @staticmethod
    def _skill_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "name": row["name"], "version": row["version"], "scope": row["scope"], "scope_id": row["scope_id"], "source": row["source"], "root_path": row["root_path"], "digest": row["digest"], "manifest": _object(row["manifest_json"]), "enabled": bool(row["enabled"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}


def _normalize_budget(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "max_steps": max(1, min(_safe_int(source.get("max_steps")) or 30, 200)),
        "max_seconds": max(10, min(_safe_int(source.get("max_seconds")) or 900, 14400)),
        "max_model_calls": max(1, min(_safe_int(source.get("max_model_calls")) or 40, 400)),
        "max_failures": max(1, min(_safe_int(source.get("max_failures")) or 3, 12)),
    }


def _binding_label(kind: str, value: str) -> str:
    if kind == "directory":
        return Path(value).name or "目录"
    return value[:100]


def _clean_label(value: object, fallback: str, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    return (text or fallback)[:limit]


SCOPE_KINDS = ("directory", "application", "domain", "game")


def _scope_values(scope: dict[str, Any], kind: str) -> set[str]:
    raw = scope.get(kind)
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def _scope_additions(current: dict[str, Any], requested: dict[str, Any]) -> dict[str, list[str]]:
    """Entries in ``requested`` that ``current`` does not already cover."""
    additions: dict[str, list[str]] = {}
    for kind in SCOPE_KINDS:
        new_values = sorted(_scope_values(requested, kind) - _scope_values(current, kind))
        if new_values:
            additions[kind] = new_values
    return additions


def _merged_scope(current: dict[str, Any], additions: dict[str, list[str]]) -> dict[str, list[str]]:
    return {kind: sorted(_scope_values(current, kind) | set(additions.get(kind, []))) for kind in SCOPE_KINDS}


def _clean_identifier(value: object, fallback: str = "") -> str:
    text = str(value or "").strip().casefold().replace(" ", "_")
    safe = "".join(char for char in text if char.isalnum() or char in "._-")
    return safe[:100] or fallback


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _array(value: object) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _event_digest(payload: dict[str, Any]) -> str:
    canonical = _json(payload)
    return "evt-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
