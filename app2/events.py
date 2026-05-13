from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time


class RouteKind(str, Enum):
    CHAT = "chat"
    CODEX = "codex"


class TaskStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class RouteDecision:
    kind: RouteKind
    text: str
    reason: str = ""


@dataclass(frozen=True)
class AppEvent:
    display_text: str
    voice_text: str = ""
    task_id: str = ""
    goal: str = ""
    status: TaskStatus | None = None
    details: str = ""
    final_message: str = ""
    artifacts: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    elapsed_seconds: float | None = None

    @property
    def is_task_event(self) -> bool:
        return bool(self.task_id and self.status is not None)

    def history_row(self, speaker: str) -> dict[str, str]:
        return {
            "speaker": speaker,
            "text": self.display_text,
            "created_at": f"{self.created_at:.3f}",
        }


def voice_line(active_lang: str, chinese: str, japanese: str) -> str:
    lang = (active_lang or "").strip().lower()
    if lang.startswith(("zh", "cn", "yue")):
        return chinese
    return japanese or chinese

