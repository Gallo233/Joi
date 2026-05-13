from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AgentEventType(str, Enum):
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    APPROVAL_REQUIRED = "approval_required"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"


@dataclass(frozen=True)
class ApprovalRequest:
    title: str
    detail: str
    capability: str = "elevated_action"


@dataclass(frozen=True)
class AgentEvent:
    type: AgentEventType
    task_id: str
    title: str
    message: str
    approval: ApprovalRequest | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class AgentTask:
    id: str
    title: str
    user_request: str
    status: AgentEventType = AgentEventType.TASK_STARTED
