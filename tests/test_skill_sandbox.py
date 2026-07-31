"""Regression tests for the Skill runner's Seatbelt profile.

The profile is the only thing standing between a Skill script and the user's
machine, so these tests check both what it says and what it actually stops. The
behavioural cases need a real macOS ``sandbox-exec`` and skip elsewhere; the
shape of the profile is checked everywhere so a broad rule cannot be
reintroduced on a machine that never runs the sandbox.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_companion.core.agent_skills import (
    FORBIDDEN_SANDBOX_RULES,
    AgentSkillService,
    sandbox_plan,
)
from agent_companion.core.collaboration_store import CollaborationStore, DEFAULT_PROJECT_ID


SKILL_MD = """---
name: sandbox-probe
description: A skill whose script is used to probe the sandbox boundary.
version: 1.0.0
license: MIT
---

Run the probe script.
"""

SANDBOX_AVAILABLE = sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file()


class SandboxProfileShapeTests(unittest.TestCase):
    """What the profile grants, checked without executing anything."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.skill = self.root / "skill"
        (self.skill / "scripts").mkdir(parents=True)
        self.script = self.skill / "scripts" / "run.py"
        self.script.write_text("print('ok')\n", encoding="utf-8")
        self.project = self.root / "project"
        self.project.mkdir()
        self.output = self.root / "out"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def plan(self, permissions: dict | None = None):
        return sandbox_plan(
            self.script,
            skill_root=self.skill,
            project=self.project,
            output_dir=self.output,
            permissions=permissions or {},
        )

    @unittest.skipUnless(SANDBOX_AVAILABLE, "sandbox-exec is macOS only")
    def test_profile_carries_none_of_the_forbidden_broad_rules(self) -> None:
        profile = self.plan().profile
        for rule in FORBIDDEN_SANDBOX_RULES:
            self.assertNotIn(rule, profile, f"release profile must not contain {rule}")
        self.assertIn("(deny default)", profile)
        self.assertIn("(deny network*)", profile)

    @unittest.skipUnless(SANDBOX_AVAILABLE, "sandbox-exec is macOS only")
    def test_only_the_output_directory_is_writable(self) -> None:
        plan = self.plan({"directories": ["project"]})
        self.assertEqual(plan.writable, (str(self.output),))
        self.assertIn(str(self.project), plan.readable)

    @unittest.skipUnless(SANDBOX_AVAILABLE, "sandbox-exec is macOS only")
    def test_an_undeclared_directory_is_not_readable(self) -> None:
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.assertNotIn(str(elsewhere), self.plan().readable)
        self.assertIn(str(elsewhere), self.plan({"directories": [str(elsewhere)]}).readable)

    @unittest.skipUnless(SANDBOX_AVAILABLE, "sandbox-exec is macOS only")
    def test_declaring_a_filesystem_root_grants_nothing(self) -> None:
        plan = self.plan({"directories": ["/", "/Users", "/etc", str(Path.home())]})
        for denied in ("/", "/Users", "/etc", str(Path.home())):
            self.assertNotIn(denied, plan.readable)

    def test_an_unsupported_script_type_has_no_plan(self) -> None:
        script = self.skill / "scripts" / "run.rb"
        script.write_text("puts 'no'\n", encoding="utf-8")
        self.assertIsNone(
            sandbox_plan(script, skill_root=self.skill, project=self.project, output_dir=self.output)
        )


@unittest.skipUnless(SANDBOX_AVAILABLE, "sandbox-exec is macOS only")
class SandboxBehaviourTests(unittest.TestCase):
    """What the profile actually stops, by running scripts inside it."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = CollaborationStore(self.workspace, data_home=self.root / "data")
        self.service = AgentSkillService(self.workspace, self.store)
        self.secret = self.root / "private-notes.txt"
        self.secret.write_text("a file the skill never declared", encoding="utf-8")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def run_script(self, body: str, *, permissions: str = "") -> dict:
        source = self.root / f"src-{abs(hash(body)) % 100000}"
        (source / "scripts").mkdir(parents=True)
        (source / "SKILL.md").write_text(
            SKILL_MD.replace("license: MIT", f"license: MIT{permissions}"), encoding="utf-8"
        )
        (source / "scripts" / "probe.py").write_text(body, encoding="utf-8")
        inspection = self.service.inspect(str(source))["inspection"]
        installed = self.service.install(
            str(source),
            scope="project",
            scope_id=DEFAULT_PROJECT_ID,
            expected_digest=inspection["digest"],
        )["skill"]
        return self.service.run(
            installed["id"],
            script="scripts/probe.py",
            permission_profile="collaborate",
            approved=True,
        )

    def test_a_script_runs_and_reports_its_own_boundary(self) -> None:
        result = self.run_script("print('ok')\n")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stdout"].strip(), "ok")
        self.assertTrue(result["sandboxed"])
        self.assertEqual(result["network"], "disabled")

    def test_a_script_cannot_read_a_file_it_did_not_declare(self) -> None:
        result = self.run_script(
            "import pathlib\n"
            f"print(pathlib.Path({str(self.secret)!r}).read_text())\n"
        )
        self.assertFalse(result["ok"], result)
        self.assertIn("PermissionError", result["stderr"])

    def test_a_script_cannot_read_the_users_home_directory(self) -> None:
        result = self.run_script(
            "import pathlib\n"
            "print(sorted(p.name for p in pathlib.Path('/Users').iterdir()))\n"
        )
        self.assertFalse(result["ok"], result)

    def test_a_script_cannot_write_outside_its_output_directory(self) -> None:
        target = self.workspace / "written-by-skill.txt"
        result = self.run_script(
            "import pathlib\n"
            f"pathlib.Path({str(target)!r}).write_text('x')\n"
        )
        self.assertFalse(result["ok"], result)
        self.assertFalse(target.exists(), "the workspace must not be writable by default")

    def test_a_script_cannot_spawn_another_program(self) -> None:
        result = self.run_script(
            "import subprocess\n"
            "print(subprocess.run(['/bin/sh', '-c', 'echo escaped'], capture_output=True).stdout)\n"
        )
        self.assertFalse(result["ok"], result)

    def test_a_script_cannot_reach_the_network(self) -> None:
        result = self.run_script(
            "import socket\n"
            "socket.create_connection(('1.1.1.1', 53), timeout=3).close()\n"
            "print('connected')\n"
        )
        self.assertFalse(result["ok"], result)
        self.assertNotIn("connected", result["stdout"])

    def test_a_declared_directory_becomes_readable(self) -> None:
        shared = self.root / "shared"
        shared.mkdir()
        (shared / "note.txt").write_text("declared input", encoding="utf-8")
        result = self.run_script(
            "import pathlib\n"
            f"print(pathlib.Path({str(shared / 'note.txt')!r}).read_text())\n",
            permissions=f"\npermissions:\n  directories:\n    - {shared}\n",
        )
        self.assertTrue(result["ok"], result)
        self.assertIn("declared input", result["stdout"])


if __name__ == "__main__":
    unittest.main()
