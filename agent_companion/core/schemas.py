from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import time
from typing import Any


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EventType(str, Enum):
    USER_MESSAGE = "user_message"
    PLAN_CREATED = "plan_created"
    RUNTIME_STARTED = "runtime_started"
    RUNTIME_DELTA = "runtime_delta"
    RUNTIME_FINAL = "runtime_final"
    RUNTIME_ERROR = "runtime_error"
    SKILL_STARTED = "skill_started"
    SKILL_COMPLETED = "skill_completed"
    APPROVAL_REQUIRED = "approval_required"
    AUDIT_EVENT = "audit_event"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"


@dataclass(frozen=True)
class DisplayCard:
    title: str
    summary: str
    body: str = ""
    status: str = "info"
    artifacts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VoiceLine:
    text: str
    emotion: str = "neutral"
    sprite: str = "1"


@dataclass(frozen=True)
class ToolRequest:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    agent_state: dict[str, Any]
    display_card: DisplayCard
    voice_line: VoiceLine
    requires_approval: bool = False
    risk: RiskLevel = RiskLevel.LOW


@dataclass(frozen=True)
class AgentEvent:
    type: EventType
    task_id: str
    display_card: DisplayCard
    voice_line: VoiceLine
    agent_state: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    event_id: str = ""
    sequence: int = 0
    project_id: str = ""
    thread_id: str = ""
    session_id: str = ""
    character_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["type"] = self.type.value
        return payload


@dataclass(frozen=True)
class AgentPlan:
    task_id: str
    user_text: str
    intent: str
    steps: list[ToolRequest]
