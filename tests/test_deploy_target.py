"""The guard that stops a deploy resetting this box onto a tree it cannot run.

The failure it exists for is quiet: `git reset --hard` onto the wrong branch
succeeds, the site keeps rendering, and only a visitor discovers that no session
can be opened. So these tests are mostly about what the guard *refuses*, and
about the one thing that has to keep passing -- this repository itself, so that
adding a broker flag without the matching Core flag fails here rather than on
the box.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_companion.web.deploy.verify_target import declared_flags, spawn_flags, unit_flags, verify


CONSISTENT_BROKER = '''
import argparse, subprocess

def _spawn_core(session_id, token, workspace, port):
    command = [
        "python", "-m", "agent_companion.core.server",
        "--workspace", str(workspace),
        "--session-token", token,
        "--allowed-methods", "user.message",
    ]
    return subprocess.Popen(command)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-base", required=True)
    parser.add_argument("--seed-workspace", required=True)
'''

CONSISTENT_CORE = '''
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--session-token", default="")
    parser.add_argument("--allowed-methods", default="")
'''

UNIT = """[Service]
ExecStart=/opt/joi/.venv/bin/python -m agent_companion.web.broker \\
  --public-base https://ljl.design \\
  --seed-workspace /opt/joi-web-seed
Restart=on-failure
"""


class DeployTargetGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self._git("init", "--quiet", "-b", "trunk")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def _commit(self, files: dict[str, str]) -> None:
        for path, body in files.items():
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "--quiet", "-m", "state")

    def _tree(self, broker: str = CONSISTENT_BROKER, core: str = CONSISTENT_CORE) -> None:
        self._commit(
            {
                "agent_companion/web/broker.py": broker,
                "agent_companion/web/prepare_seed.py": "# seed\n",
                "agent_companion/core/server.py": core,
            }
        )

    def _unit(self, text: str = UNIT) -> Path:
        path = self.repo / "joi-web.service"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_consistent_tree_deploys(self) -> None:
        self._tree()
        self.assertEqual(verify(self.repo, "HEAD", self._unit()), [])

    def test_a_branch_from_before_joi_web_is_refused_by_name(self) -> None:
        """The ordinary way this goes wrong: JOI_BRANCH defaults to main.

        The reset would succeed and the seed step would fail, leaving the box on
        a tree with no broker at all.
        """

        self._commit({"README.md": "an older branch\n"})
        problems = verify(self.repo, "HEAD", self._unit())
        self.assertTrue(any("agent_companion/web/broker.py" in row for row in problems), problems)
        # Every missing file is named, rather than only the first one found.
        self.assertTrue(any("prepare_seed.py" in row for row in problems), problems)

    def test_a_core_that_would_reject_the_brokers_arguments_is_refused(self) -> None:
        # The quiet one: the files are all there, the seed builds, and every
        # visitor session dies at launch because Core exits on an unknown flag.
        self._tree(core=CONSISTENT_CORE.replace('    parser.add_argument("--allowed-methods", default="")\n', ""))
        problems = verify(self.repo, "HEAD", self._unit())
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("--allowed-methods", problems[0])
        self.assertIn("does not accept", problems[0])

    def test_a_broker_that_would_reject_this_boxs_unit_is_refused(self) -> None:
        self._tree(broker=CONSISTENT_BROKER.replace('    parser.add_argument("--seed-workspace", required=True)\n', ""))
        problems = verify(self.repo, "HEAD", self._unit())
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("--seed-workspace", problems[0])

    def test_a_unit_for_something_else_produces_no_verdict_about_it(self) -> None:
        # A wrong --unit path should cost the second contract, not invent a
        # failure from flags that were never meant for the broker.
        other = self._unit("[Service]\nExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile\n")
        self._tree()
        self.assertEqual(verify(self.repo, "HEAD", other), [])
        self.assertEqual(unit_flags(other.read_text(encoding="utf-8")), set())

    def test_a_missing_spawn_function_is_refused_rather_than_read_as_no_flags(self) -> None:
        # An empty set of flags trivially satisfies the comparison, so a broker
        # this checker cannot read has to be an error, not a pass.
        self._tree(broker="import argparse\n")
        problems = verify(self.repo, "HEAD", self._unit())
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("_spawn_core", problems[0])

    def test_the_units_continuation_lines_are_all_read(self) -> None:
        self.assertEqual(unit_flags(UNIT), {"--public-base", "--seed-workspace"})

    def test_the_brokers_own_options_are_not_mistaken_for_cores(self) -> None:
        # Both command lines live in one file; only `_spawn_core`'s belong to
        # the broker->Core contract.
        self.assertEqual(spawn_flags(CONSISTENT_BROKER), {"--workspace", "--session-token", "--allowed-methods"})
        self.assertIn("--public-base", declared_flags(CONSISTENT_BROKER))


class ThisRepositoryDeploysTests(unittest.TestCase):
    """The guard must not refuse the tree it ships in.

    This is the test that earns the checker its keep: add a flag to the broker's
    spawn command without adding it to Core and it fails here, in CI, instead of
    on the box after the reset.
    """

    def test_the_checked_out_tree_can_run_the_deployment(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        if not (repo / ".git").exists():
            self.skipTest("not a git checkout")
        self.assertEqual(verify(repo, "HEAD", None), [])

    def test_the_shipped_unit_template_matches_the_shipped_broker(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        if not (repo / ".git").exists():
            self.skipTest("not a git checkout")
        unit = repo / "agent_companion" / "web" / "deploy" / "joi-web.service.example"
        self.assertTrue(unit.is_file(), unit)
        self.assertEqual(verify(repo, "HEAD", unit), [])


if __name__ == "__main__":
    unittest.main()
