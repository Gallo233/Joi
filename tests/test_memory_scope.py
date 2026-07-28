"""Recall is scoped before it is ranked.

A fact that belongs to one project must not surface as context in another, no
matter how well it matches the query — filtering after ranking would let it
take a slot in the context budget and leak across projects (PRD-AIM-007).
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from agent_companion.core.memory import (
    DEFAULT_RETENTION_CLASS,
    RETENTION_CLASSES,
    MemoryStore,
    normalize_retention_class,
)


PROJECT_A = "project-alpha"
PROJECT_B = "project-beta"
THREAD_A = "thread-1"
THREAD_B = "thread-2"


class RetentionClassTests(unittest.TestCase):
    def test_the_vocabulary_is_fixed(self) -> None:
        self.assertEqual(set(RETENTION_CLASSES), {"session", "project", "long_term", "protected"})

    def test_unknown_values_fall_back_to_the_default(self) -> None:
        self.assertEqual(normalize_retention_class("nonsense"), DEFAULT_RETENTION_CLASS)
        self.assertEqual(normalize_retention_class(""), DEFAULT_RETENTION_CLASS)
        self.assertEqual(normalize_retention_class("PROJECT"), "project")


class ScopedRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = MemoryStore(root / "memory.sqlite3", root / "vault.md")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_a_project_memory_is_invisible_to_another_project(self) -> None:
        self.store.remember("fact", "验收人是王工", project_id=PROJECT_A, retention_class="project")
        self.assertTrue(self.store.recall("验收人", project_id=PROJECT_A))
        self.assertEqual(self.store.recall("验收人", project_id=PROJECT_B), [])

    def test_a_session_memory_is_invisible_to_another_conversation(self) -> None:
        self.store.remember("fact", "先用这版草稿 marmot-draft-7", thread_id=THREAD_A, retention_class="session")
        self.assertTrue(self.store.recall("marmot-draft-7", thread_id=THREAD_A))
        self.assertEqual(self.store.recall("marmot-draft-7", thread_id=THREAD_B), [])

    def test_long_term_memories_reach_every_scope(self) -> None:
        self.store.remember("preference", "我喜欢深色主题", retention_class="long_term")
        for scope in ({"project_id": PROJECT_A}, {"project_id": PROJECT_B}, {"thread_id": THREAD_B}, {}):
            with self.subTest(scope=scope):
                self.assertTrue(self.store.recall("深色主题", **scope))

    def test_the_same_sentence_can_belong_to_two_projects(self) -> None:
        # A global uniqueness constraint would let whichever project saved it
        # first silently own the fact.
        first = self.store.remember("fact", "下周三评审", project_id=PROJECT_A, retention_class="project")
        second = self.store.remember("fact", "下周三评审", project_id=PROJECT_B, retention_class="project")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual([row["id"] for row in self.store.recall("评审", project_id=PROJECT_A)], [first["id"]])
        self.assertEqual([row["id"] for row in self.store.recall("评审", project_id=PROJECT_B)], [second["id"]])

    def test_the_same_sentence_in_one_scope_still_deduplicates(self) -> None:
        first = self.store.remember("fact", "下周三评审", project_id=PROJECT_A, retention_class="project")
        again = self.store.remember("fact", "下周三评审", project_id=PROJECT_A, retention_class="project")
        self.assertEqual(first["id"], again["id"])

    def test_scoping_happens_before_ranking(self) -> None:
        # The out-of-scope row is the better textual match; it must still not
        # take a slot in the returned context.
        self.store.remember("fact", "验收人是王工，验收人电话已确认", project_id=PROJECT_B, retention_class="project")
        mine = self.store.remember("fact", "验收人是李工", project_id=PROJECT_A, retention_class="project")
        results = self.store.recall("验收人", limit=5, project_id=PROJECT_A)
        self.assertEqual([row["id"] for row in results], [mine["id"]])

    def test_memories_saved_before_scoping_still_recall_everywhere(self) -> None:
        # Upgrading must not make a user's existing memories disappear.
        legacy = self.store.remember("fact", "旧记忆 badger-relay-5")
        with sqlite3.connect(self.store.path) as db:
            db.execute("update memories set project_id='', thread_id='', retention_class='long_term' where id=?", (legacy["id"],))
        self.assertTrue(self.store.recall("badger-relay-5", project_id=PROJECT_B))

    def test_browsing_is_not_scoped_even_though_recall_is(self) -> None:
        self.store.remember("fact", "只属于 A 的事 otter-note-3", project_id=PROJECT_A, retention_class="project")
        listed = [row["text"] for row in self.store.recent(20)]
        self.assertTrue(any("otter-note-3" in text for text in listed), "the library shows everything the user saved")
        self.assertEqual(self.store.recall("otter-note-3", project_id=PROJECT_B), [])


class ScopedContextTests(unittest.TestCase):
    """`context()` has more sources than recall, and every one of them leaks."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = MemoryStore(root / "memory.sqlite3", root / "vault.md")
        self.store.remember("fact", "A 项目的验收人是李工", project_id=PROJECT_A, retention_class="project")
        self.store.remember("fact", "B 项目的验收人是王工", project_id=PROJECT_B, retention_class="project")
        self.store.remember("preference", "我喜欢深色主题", retention_class="long_term")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _context_text(self, project_id: str, query: str = "验收人 主题") -> str:
        return "\n".join(str(row.get("text") or "") for row in self.store.context(8, query=query, project_id=project_id))

    def test_context_does_not_carry_another_project_forward(self) -> None:
        self.assertIn("李工", self._context_text(PROJECT_A))
        self.assertNotIn("王工", self._context_text(PROJECT_A))
        self.assertIn("王工", self._context_text(PROJECT_B))
        self.assertNotIn("李工", self._context_text(PROJECT_B))

    def test_the_profile_summary_is_scoped_too(self) -> None:
        # The profile is a digest of memories, so an unscoped one smuggles the
        # other project's facts in as a single 用户画像 line.
        profile_lines = [row for row in self.store.context(8, query="验收人", project_id=PROJECT_A) if row.get("kind") == "profile"]
        self.assertTrue(profile_lines, "precondition: a profile line is produced")
        self.assertNotIn("王工", profile_lines[0]["text"])

    def test_the_padding_that_fills_the_budget_is_scoped(self) -> None:
        # With no query the recall branch is skipped and only the "recent"
        # padding runs; it must be scoped as well.
        text = self._context_text(PROJECT_A, query="")
        self.assertNotIn("王工", text)

    def test_long_term_facts_still_reach_both(self) -> None:
        self.assertIn("深色主题", self._context_text(PROJECT_A))
        self.assertIn("深色主题", self._context_text(PROJECT_B))


class ProtectedMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = MemoryStore(root / "memory.sqlite3", root / "vault.md")
        self.protected = self.store.remember(
            "boundary",
            "不要在未确认时替我回复邮件",
            retention_class="protected",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_a_protected_memory_cannot_be_rewritten_by_default(self) -> None:
        result = self.store.update(self.protected["id"], text="可以替我回复邮件")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "protected_memory")
        self.assertEqual(self.store.memory(self.protected["id"])["text"], "不要在未确认时替我回复邮件")

    def test_a_protected_memory_cannot_be_deleted_by_default(self) -> None:
        result = self.store.delete(self.protected["id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "protected_memory")
        self.assertIsNotNone(self.store.memory(self.protected["id"]))

    def test_an_explicit_user_action_can_still_remove_it(self) -> None:
        # Protection guards against the model and routine cleanup, not against
        # the user's own decision.
        self.assertTrue(self.store.delete(self.protected["id"], allow_protected=True)["ok"])
        self.assertIsNone(self.store.memory(self.protected["id"]))

    def test_a_protected_memory_is_recallable_everywhere(self) -> None:
        self.assertTrue(self.store.recall("回复邮件", project_id=PROJECT_A))
        self.assertTrue(self.store.recall("回复邮件", thread_id=THREAD_B))

    def test_ordinary_memories_remain_editable(self) -> None:
        ordinary = self.store.remember("fact", "我住在杭州")
        self.assertTrue(self.store.update(ordinary["id"], text="我住在苏州")["ok"])
        self.assertTrue(self.store.delete(ordinary["id"])["ok"])


if __name__ == "__main__":
    unittest.main()
