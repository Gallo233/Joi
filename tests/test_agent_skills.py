from __future__ import annotations

import json
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

    def install_inspected(self, source: Path, **kwargs: object) -> dict:
        """Install the way the shell does: inspect, then install that digest."""

        inspection = self.service.inspect(str(source))["inspection"]
        return self.service.install(str(source), expected_digest=inspection["digest"], **kwargs)

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
        installed = self.install_inspected(source, scope="project", scope_id=DEFAULT_PROJECT_ID)["skill"]
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

    def test_install_refuses_a_source_nobody_inspected(self) -> None:
        source = self.make_skill()
        with self.assertRaises(AgentSkillError) as raised:
            self.service.install(str(source), scope="project", scope_id=DEFAULT_PROJECT_ID)
        self.assertEqual(raised.exception.code, "digest_required")

    def test_source_edited_between_preview_and_install_is_refused(self) -> None:
        source = self.make_skill()
        inspection = self.service.inspect(str(source))["inspection"]
        (source / "scripts").mkdir()
        (source / "scripts" / "run.py").write_text("print('added after preview')\n", encoding="utf-8")
        with self.assertRaises(AgentSkillError) as raised:
            self.service.install(str(source), expected_digest=inspection["digest"])
        self.assertEqual(raised.exception.code, "hash_changed")

    def test_update_reviews_the_refetched_content_before_installing_it(self) -> None:
        source = self.make_skill()
        installed = self.install_inspected(source, scope="project", scope_id=DEFAULT_PROJECT_ID)["skill"]
        (source / "SKILL.md").write_text(SKILL_MD + "\nA line the user has not seen.\n", encoding="utf-8")

        review = self.service.update(installed["id"])
        self.assertEqual(review["error"], "update_review_required")
        self.assertTrue(review["changed"])
        self.assertNotEqual(review["inspection"]["digest"], installed["digest"])
        self.assertEqual(self.service.store.skill_installation(installed["id"])["digest"], installed["digest"])

        applied = self.service.update(installed["id"], expected_digest=review["inspection"]["digest"])
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["skill"]["digest"], review["inspection"]["digest"])

    def test_provenance_records_what_joi_observed_not_what_the_package_claims(self) -> None:
        source = self.make_skill()
        installed = self.install_inspected(source, scope="project", scope_id=DEFAULT_PROJECT_ID)["skill"]
        provenance = installed["provenance"]
        self.assertEqual(provenance["source_kind"], "directory")
        self.assertEqual(provenance["trust"], "local")
        self.assertEqual(provenance["signature"], "absent")
        self.assertEqual(provenance["digest"], installed["digest"])
        self.assertFalse(provenance["auto_update"])

    def test_zip_provenance_pins_the_archive_that_was_read(self) -> None:
        skill = self.make_skill()
        archive = self.root / "packaged.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.write(skill / "SKILL.md", "packaged/SKILL.md")
        installed = self.install_inspected(archive, scope="project", scope_id=DEFAULT_PROJECT_ID)["skill"]
        self.assertEqual(installed["provenance"]["source_kind"], "zip")
        self.assertTrue(installed["provenance"]["resolved_ref"].startswith("sha256:"))

    def test_a_signature_joi_cannot_check_blocks_the_install(self) -> None:
        source = self.make_skill()
        signature_dir = source / ".well-known"
        signature_dir.mkdir()
        (signature_dir / "joi-skill-signature.json").write_text(
            json.dumps({"algorithm": "ed25519", "publisher": "nobody.example", "signature": "AAAA"}),
            encoding="utf-8",
        )
        with self.assertRaises(AgentSkillError) as raised:
            self.service.inspect(str(source))
        self.assertEqual(raised.exception.code, "signature_unverifiable")

    def test_a_malformed_signature_is_not_treated_as_an_unsigned_package(self) -> None:
        source = self.make_skill()
        signature_dir = source / ".well-known"
        signature_dir.mkdir()
        (signature_dir / "joi-skill-signature.json").write_text("not json at all", encoding="utf-8")
        with self.assertRaises(AgentSkillError) as raised:
            self.service.inspect(str(source))
        self.assertEqual(raised.exception.code, "signature_invalid")

    def test_declared_dependencies_are_shown_for_review_and_never_installed(self) -> None:
        source = self.make_skill(".py")
        (source / "SKILL.md").write_text(
            SKILL_MD.replace("license: MIT", "license: MIT\ndependencies:\n  - requests>=2\n"),
            encoding="utf-8",
        )
        installed = self.install_inspected(source, scope="project", scope_id=DEFAULT_PROJECT_ID)["skill"]
        self.assertEqual(installed["manifest"]["dependencies"], ["requests>=2"])
        review = self.service.run(installed["id"], script="scripts/run.py", permission_profile="collaborate")["review"]
        self.assertEqual(review["declared_dependencies"], ["requests>=2"])
        self.assertEqual(review["dependency_installation"], "never")

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
