"""Which utterance is still the current one.

Speech is slow to produce and instant to become wrong. Synthesis runs detached
from the turn that requested it, so by the time audio is ready the user may have
sent another message, cancelled, taken over, or switched character. Playing it
anyway makes Joi answer a question nobody is still asking — and worse, it makes
the character sound confident about work that was abandoned.

A generation is one user turn's worth of speech. Anything that supersedes the
turn retires the generation, and audio arriving for a retired one is dropped
rather than played (TDD §10.3, exit criterion: stale voice = 0).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any
import uuid


# Reasons a generation stops being current. Recorded so a dropped utterance can
# be explained without keeping the text itself.
RETIREMENT_REASONS = ("superseded", "cancelled", "taken_over", "character_changed", "shutdown")


@dataclass(frozen=True)
class VoiceGeneration:
    generation_id: str
    thread_id: str
    character_id: str
    run_id: str = ""
    created_at: float = field(default_factory=time.time)

    def payload(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "thread_id": self.thread_id,
            "character_id": self.character_id,
            "run_id": self.run_id,
            "created_at": self.created_at,
        }


class VoiceGenerationTracker:
    """Per-conversation record of the utterance currently allowed to play."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current: dict[str, VoiceGeneration] = {}
        self._dropped: list[dict[str, Any]] = []

    def begin(self, thread_id: str = "", *, character_id: str = "", run_id: str = "") -> VoiceGeneration:
        """Start a new turn, retiring whatever the conversation was saying."""
        key = thread_id or "__global__"
        generation = VoiceGeneration(f"gen-{uuid.uuid4().hex[:12]}", key, character_id, run_id)
        with self._lock:
            self._current[key] = generation
        return generation

    def current(self, thread_id: str = "") -> VoiceGeneration | None:
        with self._lock:
            return self._current.get(thread_id or "__global__")

    def current_id(self, thread_id: str = "") -> str:
        generation = self.current(thread_id)
        return generation.generation_id if generation else ""

    def is_current(self, generation_id: str, thread_id: str = "") -> bool:
        """An utterance with no generation is legacy and allowed through."""
        if not generation_id:
            return True
        return self.current_id(thread_id) == generation_id

    def retire(self, thread_id: str = "", reason: str = "superseded") -> str:
        """Stop the conversation from saying anything more from this turn."""
        key = thread_id or "__global__"
        with self._lock:
            generation = self._current.pop(key, None)
        if generation is None:
            return ""
        self._record_drop(generation.generation_id, key, reason)
        return generation.generation_id

    def retire_all(self, reason: str = "shutdown") -> list[str]:
        with self._lock:
            keys = list(self._current)
        return [retired for retired in (self.retire(key, reason) for key in keys) if retired]

    def retire_for_character_change(self, character_id: str) -> list[str]:
        """A different character must not finish the previous one's sentence."""
        with self._lock:
            stale = [key for key, generation in self._current.items() if generation.character_id != character_id]
        return [retired for retired in (self.retire(key, "character_changed") for key in stale) if retired]

    def drop(self, generation_id: str, thread_id: str = "", reason: str = "superseded") -> dict[str, Any]:
        """Note that a finished utterance was discarded, without its text."""
        return self._record_drop(generation_id, thread_id or "__global__", reason)

    def _record_drop(self, generation_id: str, thread_id: str, reason: str) -> dict[str, Any]:
        entry = {
            "generation_id": generation_id,
            "thread_id": thread_id,
            "reason": reason if reason in RETIREMENT_REASONS else "superseded",
            "at": time.time(),
        }
        with self._lock:
            self._dropped.append(entry)
            # Diagnostics only; unbounded growth would be a leak in a long session.
            self._dropped = self._dropped[-100:]
        return entry

    def dropped(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._dropped)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "current": {key: generation.payload() for key, generation in self._current.items()},
                "dropped_recent": len(self._dropped),
            }
