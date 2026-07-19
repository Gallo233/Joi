from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from agent_companion.core.agent_skills import AgentSkillError, AgentSkillService
from agent_companion.core.collaboration_store import CollaborationStore, DEFAULT_PROJECT_ID


SKILL_MD = """---
name: tidy-notes
description: Organize a small set of notes into a readable summary.
version: 1.0.0
author: Joi Tests
license: MIT
permissions:
  directories:
    - project
---

Read the selected notes, group related items, and ask before changing files.
"""


class AgentSkillServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = CollaborationStore(self.workspace, data_home=self.root / "data")
        self.service = AgentSkillService(self.workspace, self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def make_skill(self, script_suffix: str = "") -> Path:
        skill = self.root / f"source-{script_suffix.replace('.', '') or 'plain'}"
        skill.mkdir()
        (skill / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
        if script_suffix:
            scripts = skill / "scripts"
            scripts.mkdir()
            (scripts / f"run{script_suffix}").write_text("print('ok')\n", encoding="utf-8")
        return skill

    def test_inspect_install_hash_change_and_clean_uninstall(self) -> None:
        source = self.make_skill()
        inspection = self.service.inspect(str(source))["inspection"]
        installed = self.service.install(
            str(source),
            scope="project",
            scope_id=DEFAULT_PROJECT_ID,
            expected_digest=inspection["digest"],
        )["skill"]
        self.assertTrue(Path(installed["root_path"]).is_dir())
        self.assertTrue(self.service.validate(installed["id"])["ok"])
        (Path(installed["root_path"]) / "SKILL.md").write_text(SKILL_MD + "changed\n", encoding="utf-8")
        self.assertEqual(self.service.validate(installed["id"])["error"], "hash_changed")
        self.assertFalse(self.service.uninstall(installed["id"])["ok"])
        result = self.service.uninstall(installed["id"], confirmed=True)
        self.assertTrue(result["ok"])
        self.assertFalse(Path(installed["root_path"]).exists())

    def test_zip_path_traversal_is_rejected(self) -> None:
        archive = self.root / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("../escape.txt", "no")
            handle.writestr("skill/SKILL.md", SKILL_MD)
        with self.assertRaises(AgentSkillError) as raised:
            self.service.inspect(str(archive))
        self.assertEqual(raised.exception.code, "path_traversal")

    def test_symlink_and_unknown_script_are_rejected(self) -> None:
        skill = self.make_skill()
        target = skill / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        link = skill / "references"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable")
        with self.assertRaises(AgentSkillError) as raised:
            self.service.inspect(str(skill))
        self.assertEqual(raised.exception.code, "symlink_not_allowed")
        link.unlink()
        scripts = skill / "scripts"
        scripts.mkdir()
        (scripts / "run.ps1").write_text("Write-Host no", encoding="utf-8")
        with self.assertRaises(AgentSkillError) as raised:
            self.service.inspect(str(skill))
        self.assertEqual(raised.exception.code, "unknown_script_type")

    def test_code_skill_requires_non_observe_session_and_explicit_approval(self) -> None:
        source = self.make_skill(".py")
        installed = self.service.install(str(source), scope="project", scope_id=DEFAULT_PROJECT_ID)["skill"]
        observe = self.service.run(installed["id"], script="scripts/run.py", permission_profile="observe")
        self.assertEqual(observe["error"], "observe_session_cannot_run_scripts")
        review = self.service.run(installed["id"], script="scripts/run.py", permission_profile="collaborate")
        self.assertTrue(review["requires_approval"])
        self.assertEqual(review["review"]["network"], "disabled")
        if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
            executed = self.service.run(installed["id"], script="scripts/run.py", permission_profile="collaborate", approved=True)
            self.assertTrue(executed["ok"], executed)
            self.assertEqual(executed["stdout"].strip(), "ok")
            self.assertTrue(executed["sandboxed"])

    def test_draft_only_becomes_skill_after_approval(self) -> None:
        draft = self.store.create_skill_draft(
            DEFAULT_PROJECT_ID,
            "thread-legacy",
            "整理每日记录",
            {"description": "整理每日记录", "instructions": "Summarize the selected daily notes."},
        )
        self.assertEqual(self.service.list(project_id=DEFAULT_PROJECT_ID)["skills"], [])
        installed = self.service.install_draft(draft["id"], scope="project", scope_id=DEFAULT_PROJECT_ID)
        self.assertTrue(installed["ok"])
        self.assertEqual(self.store.get_skill_draft(draft["id"])["status"], "installed")


if __name__ == "__main__":
    unittest.main()
