"""Codex run artifacts stop accumulating, and a deleted conversation takes its own with it.

Each run writes an event log, an error output and a final summary. Nothing
removed them, so a development machine reached roughly 1500 runs and 11MB --
all of it still holding the goal text and model output of finished tasks,
including tasks whose conversation the user had since deleted.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_companion.core.collaboration_store import CollaborationStore
from agent_companion.core.event_bus import EventBus
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, VoiceLine
from agent_companion.core.server import JsonRpcBridge
from agent_companion.core.tools.codex import CodexTool


def _write_run(run_dir: Path, stamp: str) -> list[Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        run_dir / f"{stamp}.stdout.jsonl",
        run_dir / f"{stamp}.stderr.log",
        run_dir / f"{stamp}.final.txt",
    ]
    for path in paths:
        path.write_text("私人目标文本", encoding="utf-8")
    return paths


class CodexRunRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.run_dir = self.workspace / "data" / "agent_companion" / "codex_runs"

    def test_the_oldest_runs_are_dropped_once_the_window_is_full(self) -> None:
        tool = CodexTool(self.workspace, max_retained_runs=3)
        for stamp in ("1000", "1001", "1002", "1003", "1004"):
            _write_run(self.run_dir, stamp)

        tool._prune_old_runs()

        remaining = sorted({path.name.split(".", 1)[0] for path in self.run_dir.iterdir()})
        self.assertEqual(remaining, ["1002", "1003", "1004"])

    def test_a_run_is_dropped_whole_rather_than_in_pieces(self) -> None:
        # A summary whose event log is gone reads as a broken card, not an
        # expired one.
        tool = CodexTool(self.workspace, max_retained_runs=1)
        _write_run(self.run_dir, "1000")
        _write_run(self.run_dir, "1001")

        tool._prune_old_runs()

        self.assertEqual(sorted(p.name for p in self.run_dir.iterdir()),
                         ["1001.final.txt", "1001.stderr.log", "1001.stdout.jsonl"])

    def test_a_longer_stamp_is_newer_than_a_shorter_one(self) -> None:
        # Stamps are milliseconds. Sorting them as text would keep "999999" and
        # drop "1000000000000", which is thirteen years newer.
        tool = CodexTool(self.workspace, max_retained_runs=1)
        _write_run(self.run_dir, "999999")
        _write_run(self.run_dir, "1000000000000")

        tool._prune_old_runs()

        self.assertEqual({p.name.split(".", 1)[0] for p in self.run_dir.iterdir()}, {"1000000000000"})

    def test_a_window_that_is_not_full_loses_nothing(self) -> None:
        tool = CodexTool(self.workspace, max_retained_runs=5)
        for stamp in ("1000", "1001"):
            _write_run(self.run_dir, stamp)

        self.assertEqual(tool._prune_old_runs(), 0)
        self.assertEqual(len(list(self.run_dir.iterdir())), 6)


class DeletionReachesRunArtifactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        data_home = self.workspace / "data" / "agent_companion"
        self.run_dir = data_home / "codex_runs"
        self.store = CollaborationStore(self.workspace, data_home, "character-joi")
        self.addCleanup(self.store.close)
        self.bus = EventBus(data_home / "events.jsonl")
        self.bridge = object.__new__(JsonRpcBridge)
        self.bridge.collaboration = self.store
        self.bridge.workspace = self.workspace
        self.bridge.app = SimpleNamespace(bus=self.bus)

    def _conversation_with_a_run(self, stamp: str) -> tuple[str, str, list[Path]]:
        created = self.store.create_project("写码", "character-joi")
        project_id, thread_id = created["project"]["id"], created["thread"]["id"]
        paths = _write_run(self.run_dir, stamp)
        event = AgentEvent(
            EventType.TOOL_COMPLETED,
            "task-1",
            DisplayCard("Codex 任务", "已完成", artifacts=[str(p.relative_to(self.workspace).as_posix()) for p in paths]),
            VoiceLine(""),
            {"project_id": project_id, "thread_id": thread_id},
            project_id=project_id,
            thread_id=thread_id,
        )
        self.store.record_event(self.bus.emit(event))
        return project_id, thread_id, paths

    def test_deleting_a_thread_removes_the_runs_it_produced(self) -> None:
        _project_id, thread_id, paths = self._conversation_with_a_run("2000")
        _kept_project, _kept_thread, kept = self._conversation_with_a_run("2001")
        self.store.update_thread(thread_id, archived=True)

        result = self.bridge.thread_delete_command({"thread_id": thread_id, "confirmed": True})

        self.assertTrue(result["ok"])
        self.assertEqual([p for p in paths if p.exists()], [])
        self.assertEqual([p for p in kept if not p.exists()], [])

    def test_deleting_a_project_reaches_the_runs_of_the_threads_it_takes(self) -> None:
        project_id, _thread_id, paths = self._conversation_with_a_run("3000")
        self.store.update_project(project_id, archived=True)

        result = self.bridge.project_delete_command({"project_id": project_id, "confirmed": True})

        self.assertTrue(result["ok"])
        self.assertEqual([p for p in paths if p.exists()], [])

    def test_a_refused_deletion_keeps_the_runs(self) -> None:
        _project_id, thread_id, paths = self._conversation_with_a_run("4000")

        result = self.bridge.thread_delete_command({"thread_id": thread_id, "confirmed": False})

        self.assertFalse(result["ok"])
        self.assertEqual([p for p in paths if not p.exists()], [])

    def test_a_stored_path_cannot_reach_outside_the_run_directory(self) -> None:
        # Artifact paths come out of stored payloads, and this one names a file
        # to delete. A payload that walks up out of the run directory must be
        # refused rather than followed.
        outside = self.workspace / "data" / "agent_companion" / "joi.sqlite3"
        self.assertTrue(outside.is_file())

        removed = self.bridge._delete_run_artifacts([
            "data/agent_companion/codex_runs/../joi.sqlite3",
            "../../../etc/hosts",
            "data/agent_companion/events.jsonl",
        ])

        self.assertEqual(removed, 0)
        self.assertTrue(outside.is_file())


if __name__ == "__main__":
    unittest.main()
