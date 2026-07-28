"""Deleting a memory must delete it everywhere, not just from the main table.

The memory store keeps several projections of the same text -- an FTS index, a
readable Markdown vault, and archive tables used for dedupe and cleanup. A
delete that misses any of them leaves the text recallable, which is the one
thing a user asking Joi to forget something cannot tolerate.

TDD §10.2 exit criterion: recall after delete is zero.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from agent_companion.core.memory import MemoryStore


SECRET = "我的备用邮箱是 quokka-lantern-92"


class MemoryDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = MemoryStore(root / "memory.sqlite3", root / "vault.md")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _save(self, text: str, kind: str = "fact") -> int:
        saved = self.store.remember(kind, text)
        self.assertIsNotNone(saved, f"precondition: {text!r} should be storable")
        return int(saved["id"])

    def _all_stored_text(self) -> str:
        """Every place the store persists memory text."""
        parts: list[str] = []
        with sqlite3.connect(self.store.path) as db:
            tables = [row[0] for row in db.execute("select name from sqlite_master where type='table'")]
            for table in tables:
                try:
                    rows = db.execute(f"select * from {table}").fetchall()
                except sqlite3.DatabaseError:
                    continue
                parts.extend(str(value) for row in rows for value in row)
        if self.store.vault_path.is_file():
            parts.append(self.store.vault_path.read_text(encoding="utf-8"))
        return "\n".join(parts)

    def test_a_saved_memory_is_recallable(self) -> None:
        self._save(SECRET)
        self.assertTrue(self.store.recall("备用邮箱"), "precondition: the memory must be findable")

    def test_deleting_removes_it_from_recall(self) -> None:
        memory_id = self._save(SECRET)
        self.assertTrue(self.store.delete(memory_id)["ok"])
        self.assertEqual(self.store.recall("备用邮箱"), [])
        self.assertEqual(self.store.recall("quokka-lantern-92"), [])
        self.assertIsNone(self.store.memory(memory_id))

    def test_deleting_removes_it_from_every_projection(self) -> None:
        memory_id = self._save(SECRET)
        self.assertIn("quokka-lantern-92", self._all_stored_text(), "precondition: it was stored somewhere")
        self.store.delete(memory_id)
        # The point of the test: no table, index or projection still holds it.
        self.assertNotIn("quokka-lantern-92", self._all_stored_text())

    def test_the_fts_index_does_not_outlive_the_row(self) -> None:
        memory_id = self._save(SECRET)
        self.store.delete(memory_id)
        with sqlite3.connect(self.store.path) as db:
            rows = db.execute("select count(*) from memories_fts where memories_fts match ?", ("quokka",)).fetchone()
        self.assertEqual(rows[0], 0)

    def test_rewriting_a_memory_does_not_leave_the_old_text_searchable(self) -> None:
        # Subtler than delete: the row still exists, so a missing index removal
        # is invisible in the tables but keeps the previous wording findable.
        memory_id = self._save("旧文本 wombat-beacon-11")
        self.assertTrue(self.store.update(memory_id, text="新文本 aardvark-signal-22")["ok"])
        self.assertEqual(self.store.recall("wombat-beacon-11"), [])
        self.assertTrue(self.store.recall("aardvark"))
        with sqlite3.connect(self.store.path) as db:
            stale = db.execute("select count(*) from memories_fts where memories_fts match ?", ("wombat",)).fetchone()[0]
        self.assertEqual(stale, 0)

    def test_matching_the_index_after_a_delete_does_not_error(self) -> None:
        # Orphaned entries in an external-content FTS5 table make any query that
        # matches them fail with "missing row from content table".
        memory_id = self._save(SECRET)
        self.store.delete(memory_id)
        with sqlite3.connect(self.store.path) as db:
            rows = db.execute("select rowid from memories_fts where memories_fts match ?", ("quokka",)).fetchall()
        self.assertEqual(rows, [])

    def test_the_readable_vault_is_rewritten(self) -> None:
        memory_id = self._save(SECRET)
        self.store.delete(memory_id)
        if self.store.vault_path.is_file():
            self.assertNotIn("quokka-lantern-92", self.store.vault_path.read_text(encoding="utf-8"))

    def test_clearing_removes_everything(self) -> None:
        for index in range(5):
            self._save(f"记住第 {index} 条：quokka-lantern-9{index}")
        self.store.clear()
        self.assertEqual(self.store.recall("quokka"), [])
        self.assertNotIn("quokka-lantern", self._all_stored_text())

    def test_deleting_one_memory_leaves_the_others(self) -> None:
        keep = self._save("我住在杭州")
        drop = self._save(SECRET)
        self.store.delete(drop)
        self.assertEqual(self.store.recall("quokka-lantern-92"), [])
        self.assertIsNotNone(self.store.memory(keep))
        self.assertTrue(self.store.recall("杭州"))

    def test_a_deleted_memory_can_be_saved_again(self) -> None:
        # Deletion must not leave a fingerprint behind that blocks re-saving.
        memory_id = self._save(SECRET)
        self.store.delete(memory_id)
        self.assertIsNotNone(self.store.remember("fact", SECRET), "re-saving after delete should work")


class MemoryIsolationTests(unittest.TestCase):
    """Characters get separate stores; project/thread scope is not yet modelled."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_separate_stores_do_not_see_each_other(self) -> None:
        first = MemoryStore(self.root / "a.sqlite3", self.root / "a.md")
        second = MemoryStore(self.root / "b.sqlite3", self.root / "b.md")
        first.remember("fact", SECRET)
        self.assertTrue(first.recall("备用邮箱"))
        self.assertEqual(second.recall("备用邮箱"), [])

    def test_memories_carry_scope_columns(self) -> None:
        store = MemoryStore(self.root / "c.sqlite3", self.root / "c.md")
        with sqlite3.connect(store.path) as db:
            columns = {row[1] for row in db.execute("pragma table_info(memories)")}
        self.assertLessEqual({"project_id", "thread_id", "retention_class"}, columns)


if __name__ == "__main__":
    unittest.main()
