from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_companion.core.event_bus import EventBus
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, VoiceLine


def _event(task_id: str, summary: str) -> AgentEvent:
    return AgentEvent(
        EventType.USER_MESSAGE,
        task_id,
        DisplayCard("用户消息", summary),
        VoiceLine(""),
    )


class EventBusTests(unittest.TestCase):
    def test_emit_assigns_stable_identity_and_monotonic_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            bus = EventBus(path)
            observed: list[AgentEvent] = []
            bus.subscribe(observed.append)

            first = bus.emit(_event("task-1", "第一条"))
            second = bus.emit(_event("task-2", "第二条"))

            self.assertTrue(first.event_id.startswith("evt-"))
            self.assertNotEqual(first.event_id, second.event_id)
            self.assertEqual((first.sequence, second.sequence), (1, 2))
            self.assertEqual(first.agent_state["ui_phase"], "received")
            self.assertEqual(first.agent_state["ui_label"], "已收到")
            self.assertFalse(first.agent_state["ui_transient"])
            self.assertEqual(observed, [first, second])
            self.assertEqual(bus.drain(), [first, second])

            restarted = EventBus(path)
            third = restarted.emit(_event("task-3", "第三条"))
            self.assertEqual(third.sequence, 3)
            self.assertEqual([row["sequence"] for row in restarted.recent(10)], [1, 2, 3])

    def test_emit_adds_safe_public_progress_phase_without_overriding_specific_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bus = EventBus(Path(directory) / "events.jsonl")
            generic = bus.emit(
                AgentEvent(
                    EventType.RUNTIME_STARTED,
                    "task-thinking",
                    DisplayCard("Joi", "我在处理。"),
                    VoiceLine(""),
                )
            )
            specific = bus.emit(
                AgentEvent(
                    EventType.TOOL_STARTED,
                    "task-companion",
                    DisplayCard("思考中", "Joi 正在想…"),
                    VoiceLine(""),
                    {"ui_phase": "thinking", "ui_label": "Joi 正在想"},
                )
            )

            self.assertEqual(generic.agent_state["ui_phase"], "thinking")
            self.assertTrue(generic.agent_state["ui_transient"])
            self.assertEqual(specific.agent_state["ui_phase"], "thinking")
            self.assertEqual(specific.agent_state["ui_label"], "Joi 正在想")

    def test_public_phase_is_attached_and_defers_to_capability_session_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bus = EventBus(Path(directory) / "events.jsonl")
            context = {"project_id": "p", "thread_id": "t", "session_id": "s", "character_id": "c", "session_state": "running"}
            bus.set_context_provider(lambda: context)

            acting = bus.emit(AgentEvent(EventType.TOOL_STARTED, "task-1", DisplayCard("电脑操作", "点击"), VoiceLine("")))
            self.assertEqual(acting.public_phase, "acting")
            self.assertEqual(acting.agent_state["public_phase"], "acting")

            # A paused session must not keep publishing "acting".
            context["session_state"] = "paused"
            paused = bus.emit(AgentEvent(EventType.TOOL_STARTED, "task-2", DisplayCard("电脑操作", "点击"), VoiceLine("")))
            self.assertEqual(paused.public_phase, "paused")

            # A suspended session has not finished: a late completion must not
            # tell the user the work is over while they are still being waited on.
            done_while_paused = bus.emit(AgentEvent(EventType.TASK_COMPLETED, "task-3", DisplayCard("完成", "好了"), VoiceLine("")))
            self.assertEqual(done_while_paused.public_phase, "paused")

            context["session_state"] = "running"
            done = bus.emit(AgentEvent(EventType.TASK_COMPLETED, "task-4", DisplayCard("完成", "好了"), VoiceLine("")))
            self.assertEqual(done.public_phase, "done")

            # A failure stays visible even while suspended.
            context["session_state"] = "paused"
            failed = bus.emit(AgentEvent(EventType.TASK_FAILED, "task-5", DisplayCard("失败", "没跑通"), VoiceLine("")))
            self.assertEqual(failed.public_phase, "failed")

            explicit = bus.emit(AgentEvent(EventType.TOOL_STARTED, "task-4", DisplayCard("x", "y"), VoiceLine(""), public_phase="waiting"))
            self.assertEqual(explicit.public_phase, "waiting")

    def test_recent_backfills_deterministic_ids_for_legacy_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            legacy = {
                "type": "user_message",
                "task_id": "legacy-task",
                "display_card": {"title": "用户消息", "summary": "旧消息"},
                "voice_line": {"text": ""},
                "agent_state": {},
                "created_at": 1.0,
            }
            path.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")

            first = EventBus(path).recent(10)
            second = EventBus(path).recent(10)

            self.assertEqual(first[0]["sequence"], 0)
            self.assertTrue(first[0]["event_id"].startswith("legacy-"))
            self.assertEqual(first[0]["event_id"], second[0]["event_id"])


if __name__ == "__main__":
    unittest.main()
