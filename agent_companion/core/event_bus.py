from __future__ import annotations

from collections import deque
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from queue import Queue
from threading import Lock
from typing import Callable, Any
import uuid

from agent_companion.core.schemas import AgentEvent, EventType


_UI_PHASES: dict[EventType, tuple[str, str, bool]] = {
    EventType.USER_MESSAGE: ("received", "已收到", False),
    EventType.PLAN_CREATED: ("understanding", "正在理解", True),
    EventType.RUNTIME_STARTED: ("thinking", "正在思考", True),
    EventType.RUNTIME_DELTA: ("acting", "正在处理", True),
    EventType.SKILL_STARTED: ("acting", "正在使用能力", True),
    EventType.TOOL_STARTED: ("acting", "正在处理", True),
    EventType.APPROVAL_REQUIRED: ("waiting", "等待确认", False),
    EventType.RUNTIME_FINAL: ("done", "已完成", False),
    EventType.SKILL_COMPLETED: ("done", "已完成", False),
    EventType.TOOL_COMPLETED: ("done", "已完成", False),
    EventType.TASK_COMPLETED: ("done", "已完成", False),
    EventType.RUNTIME_ERROR: ("failed", "遇到问题", False),
    EventType.TOOL_FAILED: ("failed", "遇到问题", False),
    EventType.TASK_FAILED: ("failed", "没有完成", False),
}

# The coarse phase every public event carries, so any surface reading the event
# stream -- chat, capability card, audit -- agrees on what Joi was doing without
# replaying the whole thread.
PUBLIC_PHASES = ("idle", "received", "understanding", "thinking", "acting", "waiting", "paused", "done", "failed")

# While a capability session is held, its state outranks the per-event phase: a
# paused session must not keep publishing "acting" just because a late tool
# event arrived.
_SESSION_PHASE_OVERRIDES = {"paused": "paused", "waiting_approval": "waiting"}
# "done" is overridable too. A suspended session has not finished, so a late
# completion event must not tell the user the work is over while the session is
# still waiting on them. "failed" is left alone -- a failure stays visible.
_OVERRIDABLE_PHASES = {"understanding", "thinking", "acting", "done"}


def derive_public_phase(event: AgentEvent, session_state: str = "") -> str:
    """The phase an event will publish, computed before it is emitted.

    Expression runs ahead of `emit()`, so it cannot read the field the bus is
    about to write. Deriving it here keeps both on one definition instead of a
    second copy that can drift.
    """
    phase, _label, _transient = _UI_PHASES.get(event.type, ("idle", "", False))
    return _public_phase(event, phase, session_state)


def _public_phase(event: AgentEvent, ui_phase: str, session_state: str) -> str:
    if event.public_phase in PUBLIC_PHASES:
        return event.public_phase
    override = _SESSION_PHASE_OVERRIDES.get(session_state, "")
    if override and ui_phase in _OVERRIDABLE_PHASES:
        return override
    return ui_phase if ui_phase in PUBLIC_PHASES else "idle"


class EventBus:
    def __init__(self, event_path: Path) -> None:
        self.event_path = event_path
        self._queue: Queue[AgentEvent] = Queue()
        self._subscribers: list[Callable[[AgentEvent], None]] = []
        self._lock = Lock()
        self._sequence = self._load_latest_sequence()
        self._context_provider: Callable[[], dict[str, Any]] | None = None

    def subscribe(self, callback: Callable[[AgentEvent], None]) -> None:
        self._subscribers.append(callback)

    def set_context_provider(self, provider: Callable[[], dict[str, Any]] | None) -> None:
        self._context_provider = provider

    @property
    def latest_sequence(self) -> int:
        with self._lock:
            return self._sequence

    def emit(self, event: AgentEvent) -> AgentEvent:
        with self._lock:
            self._sequence += 1
            state = dict(event.agent_state or {})
            context: dict[str, Any] = {}
            if self._context_provider is not None:
                try:
                    context = dict(self._context_provider() or {})
                except Exception:
                    context = {}
            phase, label, transient = _UI_PHASES.get(event.type, ("idle", "状态已更新", False))
            state.setdefault("ui_phase", phase)
            state.setdefault("ui_label", label)
            state.setdefault("ui_transient", transient)
            public_phase = _public_phase(event, phase, str(context.get("session_state") or ""))
            state.setdefault("public_phase", public_phase)
            for key in ("project_id", "thread_id", "session_id", "character_id"):
                value = getattr(event, key, "") or context.get(key) or state.get(key) or ""
                if value:
                    state.setdefault(key, str(value))
            enriched = replace(
                event,
                agent_state=state,
                event_id=event.event_id or f"evt-{uuid.uuid4().hex}",
                sequence=self._sequence,
                project_id=event.project_id or str(context.get("project_id") or state.get("project_id") or ""),
                thread_id=event.thread_id or str(context.get("thread_id") or state.get("thread_id") or ""),
                session_id=event.session_id or str(context.get("session_id") or state.get("session_id") or ""),
                character_id=event.character_id or str(context.get("character_id") or state.get("character_id") or ""),
                public_phase=public_phase,
            )
            self.event_path.parent.mkdir(parents=True, exist_ok=True)
            with self.event_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(enriched.to_dict(), ensure_ascii=False) + "\n")
        self._queue.put(enriched)
        for callback in list(self._subscribers):
            try:
                callback(enriched)
            except Exception:
                continue
        return enriched

    def recent(self, limit: int = 160, after_sequence: int = 0) -> list[dict]:
        safe_limit = max(1, min(int(limit), 400))
        if not self.event_path.is_file():
            return []
        try:
            with self.event_path.open("r", encoding="utf-8") as handle:
                lines = deque(handle, maxlen=safe_limit if after_sequence <= 0 else 400)
        except OSError:
            return []
        rows: list[dict] = []
        for line in lines:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            sequence = _safe_int(row.get("sequence"))
            if after_sequence > 0 and sequence <= after_sequence:
                continue
            row["sequence"] = sequence
            row["event_id"] = str(row.get("event_id") or _legacy_event_id(row))
            rows.append(row)
        return rows[-safe_limit:]

    def drain(self) -> list[AgentEvent]:
        rows: list[AgentEvent] = []
        while not self._queue.empty():
            rows.append(self._queue.get())
        return rows

    def _load_latest_sequence(self) -> int:
        if not self.event_path.is_file():
            return 0
        try:
            with self.event_path.open("r", encoding="utf-8") as handle:
                lines = deque(handle, maxlen=400)
        except OSError:
            return 0
        latest = 0
        for line in lines:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(row, dict):
                latest = max(latest, _safe_int(row.get("sequence")))
        return latest


def _safe_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _legacy_event_id(row: dict) -> str:
    canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"legacy-{digest}"
