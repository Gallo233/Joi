from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from types import SimpleNamespace

from unittest.mock import patch

from agent_companion.core import event_bus
from agent_companion.core.collaboration_store import CollaborationStore
from agent_companion.core.event_bus import _EVENT_LOG_TRIM_BYTES, _READ_WINDOW, EventBus
from agent_companion.core.server import JsonRpcBridge
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, VoiceLine


def _event(task_id: str, summary: str) -> AgentEvent:
    return AgentEvent(
        EventType.USER_MESSAGE,
        task_id,
        DisplayCard("用户消息", summary),
        VoiceLine(""),
    )


def _scoped_event(task_id: str, summary: str, project_id: str, thread_id: str) -> AgentEvent:
    return AgentEvent(
        EventType.USER_MESSAGE,
        task_id,
        DisplayCard("用户消息", summary),
        VoiceLine(summary),
        {"project_id": project_id, "thread_id": thread_id},
        project_id=project_id,
        thread_id=thread_id,
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

    def test_forget_removes_a_deleted_conversation_and_keeps_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            bus = EventBus(path)
            bus.emit(_scoped_event("task-1", "私人内容", "project-a", "thread-a"))
            bus.emit(_scoped_event("task-2", "工作内容", "project-b", "thread-b"))

            removed = bus.forget(thread_ids=["thread-a"])

            body = path.read_text(encoding="utf-8")
            self.assertEqual(removed, 1)
            self.assertNotIn("私人内容", body)
            self.assertIn("工作内容", body)

    def test_forget_reaches_rows_that_only_carry_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            bus = EventBus(path)
            bus.emit(_scoped_event("task-1", "私人内容", "project-a", "thread-a"))

            self.assertEqual(bus.forget(project_ids=["project-a"]), 1)
            self.assertNotIn("私人内容", path.read_text(encoding="utf-8"))

    def test_forget_without_a_target_leaves_the_log_alone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            bus = EventBus(path)
            bus.emit(_scoped_event("task-1", "私人内容", "project-a", "thread-a"))
            before = path.read_text(encoding="utf-8")

            self.assertEqual(bus.forget(thread_ids=[""], project_ids=[]), 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_forget_keeps_the_sequence_from_moving_backwards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            bus = EventBus(path)
            bus.emit(_scoped_event("task-1", "留下", "project-b", "thread-b"))
            bus.emit(_scoped_event("task-2", "删掉", "project-a", "thread-a"))
            highest = bus.latest_sequence

            bus.forget(thread_ids=["thread-a"])

            # The newest rows went with the deleted thread, so a restart would
            # otherwise reissue numbers the surviving rows already hold.
            self.assertEqual(EventBus(path).latest_sequence, highest)
            self.assertEqual(EventBus(path).recent(10)[-1]["display_card"]["summary"], "留下")

    def test_the_log_keeps_a_tail_instead_of_every_event_ever_written(self) -> None:
        # The real ceiling is megabytes; shrink it, and the window a reader
        # needs, so the test proves the bound without writing them.
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(event_bus, "_EVENT_LOG_TRIM_BYTES", 40_000), \
                patch.object(event_bus, "_READ_WINDOW", 10):
            path = Path(directory) / "events.jsonl"
            bus = EventBus(path, max_records=200)
            padding = "x" * 1_000
            for index in range(400):
                bus.emit(_event(f"task-{index}", f"{index}-{padding}"))

            rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertLessEqual(len(rows), 200)
            # A trim has to land under its own trigger, or the log rewrites
            # itself on every event from here on.
            self.assertLess(path.stat().st_size, 40_000)
            # Trimming keeps the newest rows, so the restored counter is intact
            # and the oldest ones are the ones that went.
            self.assertEqual(bus.latest_sequence, 400)
            self.assertEqual(EventBus(path).latest_sequence, 400)
            self.assertNotIn('"task-0"', path.read_text(encoding="utf-8"))
            self.assertIn('"task-399"', path.read_text(encoding="utf-8"))

    def test_a_log_of_oversized_rows_stops_rewriting_itself_on_every_event(self) -> None:
        # Rows a reader needs can outweigh the trigger on their own. The log
        # cannot shrink past them, so it must stop trying rather than rewrite
        # itself for every event that follows.
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(event_bus, "_EVENT_LOG_TRIM_BYTES", 20_000), \
                patch.object(event_bus, "_READ_WINDOW", 40):
            path = Path(directory) / "events.jsonl"
            bus = EventBus(path, max_records=40)
            padding = "x" * 4_000
            for index in range(60):
                bus.emit(_event(f"task-{index}", f"{index}-{padding}"))
            self.assertGreater(path.stat().st_size, 20_000)

            with patch.object(EventBus, "_read_rows", side_effect=AssertionError("rewrote the log")) as blocked:
                bus.emit(_event("task-next", "一条新事件"))

            self.assertEqual(blocked.call_count, 0)


class DeletionReachesTheEventLogTests(unittest.TestCase):
    """A confirmed deletion has to reach the compatibility log, not just SQLite.

    `EventBusTests` proves `forget()` works when it is called. These prove the
    RPC calls it -- the gap that let a deleted conversation keep every card and
    voice line it ever carried in `events.jsonl`.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        workspace = Path(self.temporary.name)
        data_home = workspace / "data" / "agent_companion"
        self.store = CollaborationStore(workspace, data_home, "character-joi")
        self.addCleanup(self.store.close)
        self.path = data_home / "events.jsonl"
        self.bus = EventBus(self.path)
        self.bridge = object.__new__(JsonRpcBridge)
        self.bridge.collaboration = self.store
        self.bridge.app = SimpleNamespace(bus=self.bus)

    def _say(self, summary: str, project_id: str, thread_id: str) -> None:
        self.store.record_event(self.bus.emit(_scoped_event("task", summary, project_id, thread_id)))

    def test_deleting_a_thread_removes_what_it_said_from_the_log(self) -> None:
        created = self.store.create_project("私人", "character-joi")
        project_id, thread_id = created["project"]["id"], created["thread"]["id"]
        self._say("私人内容", project_id, thread_id)
        self.store.update_thread(thread_id, archived=True)

        result = self.bridge.thread_delete_command({"thread_id": thread_id, "confirmed": True})

        self.assertTrue(result["ok"])
        self.assertEqual(self.store.history(thread_id), [])
        self.assertNotIn("私人内容", self.path.read_text(encoding="utf-8"))

    def test_deleting_a_project_reaches_the_threads_it_takes_with_it(self) -> None:
        created = self.store.create_project("私人", "character-joi")
        project_id, thread_id = created["project"]["id"], created["thread"]["id"]
        self._say("私人内容", project_id, thread_id)
        # A row logged before events carried a project id is reachable by thread
        # alone, so the command has to read the threads before they cascade away.
        self.bus.emit(_scoped_event("legacy", "旧的私人内容", "", thread_id))
        self.store.update_project(project_id, archived=True)

        result = self.bridge.project_delete_command({"project_id": project_id, "confirmed": True})

        body = self.path.read_text(encoding="utf-8")
        self.assertTrue(result["ok"])
        self.assertNotIn("私人内容", body)
        self.assertNotIn("旧的私人内容", body)

    def test_a_refused_deletion_leaves_the_log_alone(self) -> None:
        created = self.store.create_project("私人", "character-joi")
        project_id, thread_id = created["project"]["id"], created["thread"]["id"]
        self._say("私人内容", project_id, thread_id)

        # Not archived and not confirmed: the store refuses, so nothing is lost.
        result = self.bridge.thread_delete_command({"thread_id": thread_id, "confirmed": False})

        self.assertFalse(result["ok"])
        self.assertIn("私人内容", self.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
