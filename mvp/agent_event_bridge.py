from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mvp.agent_runtime_types import AgentEvent, AgentEventType, ApprovalRequest


class AgentEventBridge:
    """Read external task events from an append-only JSONL inbox."""

    def __init__(self, inbox_path: Path, source_name: str = "inbox") -> None:
        self.inbox_path = inbox_path
        self.source_name = source_name
        self._position = inbox_path.stat().st_size if inbox_path.is_file() else 0

    def read_new_events(self) -> list[AgentEvent]:
        if not self.inbox_path.is_file():
            return []
        current_size = self.inbox_path.stat().st_size
        if current_size < self._position:
            self._position = 0
        events: list[AgentEvent] = []
        with self.inbox_path.open("r", encoding="utf-8") as handle:
            handle.seek(self._position)
            for line in handle:
                event = self._parse_line(line)
                if event is not None:
                    metadata = {**event.metadata, "source": self.source_name}
                    event = AgentEvent(
                        type=event.type,
                        task_id=event.task_id,
                        title=event.title,
                        message=event.message,
                        approval=event.approval,
                        metadata=metadata,
                    )
                    events.append(event)
            self._position = handle.tell()
        return events

    def _parse_line(self, line: str) -> AgentEvent | None:
        line = line.strip()
        if not line:
            return None
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(raw, dict):
            return None
        return event_from_mapping(raw)


def append_external_event(inbox_path: Path, event: dict[str, Any]) -> None:
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    with inbox_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def event_from_mapping(raw: dict[str, Any]) -> AgentEvent | None:
    event_type = _normalize_event_type(str(raw.get("type", "")))
    if event_type is None:
        return None
    title = str(raw.get("title") or "外部任务")
    message = str(raw.get("message") or title)
    task_id = str(raw.get("task_id") or "external")
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    detail = str(raw.get("detail") or "")
    if detail:
        metadata = {**metadata, "detail": detail}

    approval = None
    if event_type == AgentEventType.APPROVAL_REQUIRED:
        approval = ApprovalRequest(
            title=title,
            detail=detail or message,
            capability=str(raw.get("capability") or "external_action"),
        )
    return AgentEvent(
        type=event_type,
        task_id=task_id,
        title=title,
        message=message,
        approval=approval,
        metadata={str(key): str(value) for key, value in metadata.items()},
    )


def _normalize_event_type(value: str) -> AgentEventType | None:
    aliases = {
        "start": AgentEventType.TASK_STARTED,
        "started": AgentEventType.TASK_STARTED,
        "task_started": AgentEventType.TASK_STARTED,
        "progress": AgentEventType.TASK_PROGRESS,
        "task_progress": AgentEventType.TASK_PROGRESS,
        "approval": AgentEventType.APPROVAL_REQUIRED,
        "approval_required": AgentEventType.APPROVAL_REQUIRED,
        "complete": AgentEventType.TASK_COMPLETED,
        "completed": AgentEventType.TASK_COMPLETED,
        "task_completed": AgentEventType.TASK_COMPLETED,
        "failed": AgentEventType.TASK_FAILED,
        "task_failed": AgentEventType.TASK_FAILED,
    }
    return aliases.get(value.strip().casefold())
