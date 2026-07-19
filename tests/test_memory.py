from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_companion.core.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def make_store(self, root: Path) -> MemoryStore:
        return MemoryStore(root / "memory.sqlite3", root / "memory" / "joi_memory_vault.md")

    def test_remember_deduplicates_normalized_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(Path(directory))

            first = store.remember("preference", "用户更喜欢简洁的蓝白界面。", source="chat")
            second = store.remember("note", " 用户更喜欢简洁的蓝白界面 ", source="chat")

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertEqual(first["id"], second["id"])
            self.assertTrue(second["deduplicated"])
            self.assertEqual(store.counts()["saved"], 1)

    def test_existing_duplicates_are_archived_during_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "memory.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute(
                    """
                    create table memories(
                        id integer primary key autoincrement,
                        kind text not null,
                        text text not null,
                        source text not null default 'legacy',
                        created_at real not null,
                        ephemeral integer not null default 0,
                        sensitive integer not null default 0
                    )
                    """
                )
                db.executemany(
                    "insert into memories(kind, text, source, created_at) values (?, ?, ?, ?)",
                    [
                        ("note", "用户喜欢短回答。", "legacy", 1.0),
                        ("note", "用户喜欢短回答", "legacy", 2.0),
                        ("project", "正在重构 Joi 记忆模块", "legacy", 3.0),
                    ],
                )

            store = self.make_store(root)

            self.assertEqual(store.counts()["saved"], 2)
            with sqlite3.connect(path) as db:
                archived = db.execute("select original_id, duplicate_of from memory_dedupe_archive").fetchall()
            self.assertEqual(archived, [(2, 1)])

    def test_list_update_and_duplicate_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(Path(directory))
            first = store.remember("preference", "回答保持简洁", source="manual")
            second = store.remember("project", "最近在优化记忆模块", source="manual")

            page = store.list_memories(limit=1)
            self.assertEqual(page["total"], 2)
            self.assertEqual(len(page["items"]), 1)
            self.assertTrue(page["has_more"])

            updated = store.update(second["id"], text="最近在优化长期记忆模块", kind="project")
            self.assertTrue(updated["ok"])
            self.assertEqual(store.list_memories(query="长期记忆")["total"], 1)

            collision = store.update(second["id"], text="回答保持简洁", kind="preference")
            self.assertFalse(collision["ok"])
            self.assertEqual(collision["error"], "duplicate_memory")
            self.assertEqual(store.memory(first["id"])["text"], "回答保持简洁")

    def test_chinese_recall_uses_phrase_bigrams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(Path(directory))
            store.remember("preference", "用户偏好柔和圆角和蓝白配色", source="chat")
            store.remember("project", "正在调整语音识别设置", source="chat")

            results = store.recall("蓝白界面偏好", 3)

            self.assertTrue(results)
            self.assertIn("蓝白配色", results[0]["text"])

    def test_operational_receipts_are_archived_not_profiled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "memory.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute(
                    """
                    create table memories(
                        id integer primary key autoincrement,
                        kind text not null,
                        text text not null,
                        source text not null default 'legacy',
                        created_at real not null,
                        ephemeral integer not null default 0,
                        sensitive integer not null default 0
                    )
                    """
                )
                db.execute(
                    "insert into memories(kind, text, source, created_at) values ('task_result', 'coding: Codex 已完成本次写码任务。', 'legacy', 1)"
                )

            store = self.make_store(root)

            self.assertEqual(store.counts()["saved"], 0)
            self.assertIsNone(store.remember("task_result", "companion_chat: 我听到了：你好", source="task_result"))
            self.assertIsNone(store.remember("task_outcome", "用户让 Joi 处理工程任务：优化界面", source="codex"))
            self.assertIsNone(store.remember("runtime_outcome", "用户让 Joi 检查实时状态", source="joi"))
            with sqlite3.connect(path) as db:
                archived = db.execute("select reason from memory_cleanup_archive").fetchall()
            self.assertEqual(archived, [("operational_result",)])


if __name__ == "__main__":
    unittest.main()
