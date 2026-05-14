from __future__ import annotations

import json
from pathlib import Path
from queue import Queue
from typing import Callable

from agent_companion.core.schemas import AgentEvent


class EventBus:
    def __init__(self, event_path: Path) -> None:
        self.event_path = event_path
        self._queue: Queue[AgentEvent] = Queue()
        self._subscribers: list[Callable[[AgentEvent], None]] = []

    def subscribe(self, callback: Callable[[AgentEvent], None]) -> None:
        self._subscribers.append(callback)

    def emit(self, event: AgentEvent) -> None:
        self._queue.put(event)
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        with self.event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        for callback in list(self._subscribers):
            try:
                callback(event)
            except Exception:
                continue

    def drain(self) -> list[AgentEvent]:
        rows: list[AgentEvent] = []
        while not self._queue.empty():
            rows.append(self._queue.get())
        return rows
