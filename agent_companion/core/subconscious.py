"""Subconscious Loop — Background companion intelligence.

Inspired by OpenHuman's subconscious system. Runs as a background
heartbeat that periodically:
1. Evaluates pending memory candidates
2. Maintains the memory vault
3. Checks for actionable reminders/tasks
4. Generates proactive observations when idle

The loop is lightweight — it sleeps between ticks and only does
meaningful work when there's something to do.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class SubconsciousTick:
    """Result of a single subconscious tick."""
    tick_id: str = ""
    timestamp: float = 0.0
    actions_taken: list[str] = field(default_factory=list)
    memories_reviewed: int = 0
    memories_approved: int = 0
    vault_refreshed: bool = False
    proactive_message: str = ""
    error: str = ""


@dataclass
class SubconsciousState:
    """Current state of the subconscious loop."""
    active: bool = False
    tick_count: int = 0
    last_tick_at: float = 0.0
    last_actions: list[str] = field(default_factory=list)
    total_memories_reviewed: int = 0
    total_memories_approved: int = 0
    started_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "tick_count": self.tick_count,
            "last_tick_at": self.last_tick_at,
            "last_actions": self.last_actions[-5:],
            "total_memories_reviewed": self.total_memories_reviewed,
            "total_memories_approved": self.total_memories_approved,
            "uptime_seconds": time.time() - self.started_at if self.started_at else 0,
        }


class SubconsciousLoop:
    """Background heartbeat loop for the companion agent.

    Args:
        memory: MemoryStore instance for memory operations
        interval_seconds: Seconds between ticks (default 120 = 2 minutes)
        on_proactive: Callback when the loop generates a proactive message
        on_tick: Callback after each tick (for logging/UI updates)
    """

    def __init__(
        self,
        memory: Any = None,
        interval_seconds: float = 120.0,
        on_proactive: Callable[[str, str], None] | None = None,
        on_tick: Callable[[SubconsciousTick], None] | None = None,
    ) -> None:
        self.memory = memory
        self.interval_seconds = max(30.0, interval_seconds)
        self.on_proactive = on_proactive
        self.on_tick = on_tick
        self.state = SubconsciousState()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def is_running(self) -> bool:
        return self.state.active and self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the subconscious loop in a background thread."""
        if self.is_running:
            return
        self._stop_event.clear()
        self.state.active = True
        self.state.started_at = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True, name="subconscious")
        self._thread.start()
        logger.info("Subconscious loop started (interval=%.0fs)", self.interval_seconds)

    def stop(self) -> None:
        """Stop the subconscious loop."""
        self._stop_event.set()
        self.state.active = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None
        logger.info("Subconscious loop stopped")

    def snapshot(self) -> SubconsciousState:
        """Return current state snapshot."""
        return self.state

    def _run(self) -> None:
        """Main loop body — runs in background thread."""
        while not self._stop_event.is_set():
            try:
                tick = self._do_tick()
                self.state.tick_count += 1
                self.state.last_tick_at = time.time()
                self.state.last_actions = tick.actions_taken
                self.state.total_memories_reviewed += tick.memories_reviewed
                self.state.total_memories_approved += tick.memories_approved

                if self.on_tick:
                    try:
                        self.on_tick(tick)
                    except Exception:
                        pass

                if tick.proactive_message and self.on_proactive:
                    try:
                        self.on_proactive(tick.proactive_message, tick.tick_id)
                    except Exception:
                        pass

            except Exception as exc:
                logger.warning("Subconscious tick error: %s", exc)

            # Sleep in small increments so we can respond to stop quickly
            for _ in range(int(self.interval_seconds * 2)):
                if self._stop_event.is_set():
                    return
                time.sleep(0.5)

    def _do_tick(self) -> SubconsciousTick:
        """Execute one subconscious tick."""
        import uuid
        tick = SubconsciousTick(
            tick_id=f"tick-{uuid.uuid4().hex[:8]}",
            timestamp=time.time(),
        )

        if self.memory is None:
            tick.error = "no_memory_store"
            return tick

        # Step 1: Auto-approve low-risk memory candidates
        try:
            self._auto_approve_memories(tick)
        except Exception as exc:
            tick.actions_taken.append(f"memory_review_error: {exc}")

        # Step 2: Refresh vault if memories changed
        try:
            if tick.memories_approved > 0:
                self.memory._rewrite_vault()
                tick.vault_refreshed = True
                tick.actions_taken.append("vault_refreshed")
        except Exception as exc:
            tick.actions_taken.append(f"vault_refresh_error: {exc}")

        # Step 3: Generate proactive observation if idle
        try:
            self._maybe_proactive(tick)
        except Exception as exc:
            tick.actions_taken.append(f"proactive_error: {exc}")

        if not tick.actions_taken:
            tick.actions_taken.append("no_action_needed")

        return tick

    def _auto_approve_memories(self, tick: SubconsciousTick) -> None:
        """Auto-approve low-risk pending memory candidates."""
        try:
            pending = self.memory.pending(limit=5)
        except Exception:
            return

        tick.memories_reviewed = len(pending)

        for candidate in pending:
            text = str(candidate.get("text") or "")
            kind = str(candidate.get("kind") or "note")
            candidate_id = candidate.get("id")

            if candidate_id is None:
                continue

            # Auto-approve if:
            # - It's a preference or fact (low risk)
            # - Text is reasonable length
            # - No rejection keywords
            should_approve = (
                kind in ("preference", "fact", "user_note", "note", "relationship")
                and len(text) < 500
                and not any(kw in text.lower() for kw in ("password", "secret", "token", "key", "api"))
            )

            if should_approve:
                try:
                    self.memory.save_candidate(int(candidate_id))
                    tick.memories_approved += 1
                    tick.actions_taken.append(f"approved_memory:{candidate_id}")
                except Exception:
                    pass

    def _maybe_proactive(self, tick: SubconsciousTick) -> None:
        """Generate a proactive message if conditions are right."""
        # Only generate proactive messages occasionally (every ~5 ticks)
        if self.state.tick_count % 5 != 0:
            return

        # Check if there are interesting recent memories to summarize
        try:
            recent = self.memory.recent(limit=3)
        except Exception:
            return

        if not recent:
            return

        # Build a brief proactive observation
        kinds = set()
        for mem in recent:
            kinds.add(str(mem.get("kind") or "note"))

        if "preference" in kinds:
            tick.proactive_message = "我注意到你最近的偏好变化，已经记住了。"
        elif "fact" in kinds:
            tick.proactive_message = "我整理了一下最近学到的信息。"
        elif len(recent) >= 3:
            tick.proactive_message = "我在后台整理记忆，一切正常。"

        if tick.proactive_message:
            tick.actions_taken.append("proactive_generated")
