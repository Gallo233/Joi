"""Core must not gain a way to load code that did not ship with it.

TDD §11.1 and ADR-007: a code-bearing extension runs in the out-of-host
sandboxed runner and never imports into this process. Joi used to carry a
plugin loader that executed any .py file found in a plugins directory, which
put third-party code inside the same process as approvals, secrets and the
collaboration database. This test fails if such a path comes back.
"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


CORE = Path(__file__).resolve().parent.parent / "agent_companion" / "core"

# Evaluating source text. Only meaningful as builtins: `re.compile` and
# `QApplication.exec` are unrelated methods that happen to share a name.
FORBIDDEN_BUILTINS = {"exec", "eval", "compile", "__import__"}

# Loading a module from a path, however it is spelled.
FORBIDDEN_LOADERS = {
    "exec_module",
    "spec_from_file_location",
    "module_from_spec",
    "load_source",
    "load_module",
    "SourceFileLoader",
    "SourcelessFileLoader",
    "ExtensionFileLoader",
}


def _forbidden_call(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        if func.id in FORBIDDEN_BUILTINS or func.id in FORBIDDEN_LOADERS:
            return func.id
        return ""
    if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_LOADERS:
        return func.attr
    return ""


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


class CoreCodeBoundaryTests(unittest.TestCase):
    def core_modules(self) -> list[Path]:
        modules = sorted(path for path in CORE.rglob("*.py") if "__pycache__" not in path.parts)
        self.assertTrue(modules, "expected to find Core modules to scan")
        return modules

    def test_core_never_loads_a_module_from_a_path(self) -> None:
        offenders: list[str] = []
        for path in self.core_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                offender = _forbidden_call(node)
                if offender:
                    offenders.append(f"{path.relative_to(CORE.parent.parent)}:{node.lineno} {offender}()")
        self.assertEqual(
            offenders,
            [],
            "Core must not load or evaluate code it did not ship with; "
            "extensions belong in the sandboxed Skill runner:\n" + "\n".join(offenders),
        )

    def test_dynamic_imports_name_a_literal_module(self) -> None:
        """`import_module` may probe for an optional dependency, nothing more."""

        offenders: list[str] = []
        for path in self.core_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _called_name(node) != "import_module":
                    continue
                first = node.args[0] if node.args else None
                if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                    offenders.append(f"{path.relative_to(CORE.parent.parent)}:{node.lineno}")
        self.assertEqual(offenders, [], "a dynamic import must name a fixed module:\n" + "\n".join(offenders))

    def test_no_plugin_directory_is_discovered_or_shipped(self) -> None:
        self.assertFalse(
            (CORE.parent / "plugins").exists(),
            "agent_companion/plugins was an auto-executed drop directory; it must stay removed",
        )
        for path in self.core_modules():
            source = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "discover_plugins",
                source,
                f"{path.name} reintroduces plugin discovery",
            )


if __name__ == "__main__":
    unittest.main()
