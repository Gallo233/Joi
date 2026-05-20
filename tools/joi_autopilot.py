#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
REVIEW_HANDOFF = DOCS / "REVIEW_HANDOFF.md"
AUTOPILOT_LOG = DOCS / "AUTOPILOT_LOG.md"
DEFAULT_MODEL = os.environ.get("JOI_AUTOPILOT_MODEL", "gpt-5.4")
SAFE_BRANCH_PREFIXES = ("codex/", "joi-autopilot/", "autopilot/")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _run(args: Sequence[str], *, cwd: Path = ROOT, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return _run(["git", *args])


def _which(name: str) -> str:
    return shutil.which(name) or ""


def _current_branch() -> str:
    result = _git(["branch", "--show-current"])
    return result.stdout.strip() if result.returncode == 0 else ""


def _is_git_repo() -> bool:
    result = _git(["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def _codex_login_status() -> tuple[bool, str]:
    if not _which("codex"):
        return False, "codex missing"
    result = _run(["codex", "login", "status"])
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if result.returncode != 0:
        return False, output or "login status failed"
    if "Logged in using ChatGPT" in output:
        return True, "ChatGPT subscription login"
    if "Logged in using API" in output or "API key" in output:
        return False, "API-key login detected; use ChatGPT login to avoid API billing"
    return True, output or "logged in"


def _has_unexpected_dirty_entries() -> bool:
    result = _git(["status", "--short"])
    if result.returncode != 0:
        return True
    allowed_untracked = {
        ".DS_Store",
        "docs/.DS_Store",
        "docs/HERMES_HANDOFF.md",
    }
    allowed_paths = {
        "docs/AUTOPILOT_LOG.md",
        "docs/AUTOPILOT_NATIVE_WORKFLOW.md",
        "docs/AUTOPILOT_RUNTIME_SPIKE.md",
        "docs/REVIEW_HANDOFF.md",
        "requirements-autopilot.txt",
        "tools/joi_autopilot.py",
    }
    for line in result.stdout.splitlines():
        path = line[3:] if len(line) > 3 else line
        if line.startswith("?? ") and path in allowed_untracked:
            continue
        if path in allowed_paths or path.startswith("docs/AUTOPILOT_"):
            continue
        return True
    return False


def preflight(*, strict: bool = False) -> int:
    branch = _current_branch()
    login_ok, login_detail = _codex_login_status()
    dirty = _has_unexpected_dirty_entries()
    checks = [
        Check("git repo", _is_git_repo(), str(ROOT)),
        Check("branch", bool(branch), branch or "unknown"),
        Check("safe branch", branch != "main" and branch.startswith(SAFE_BRANCH_PREFIXES), branch or "unknown"),
        Check("clean enough", not dirty, "no unexpected dirty code" if not dirty else "unexpected dirty worktree entries"),
        Check("codex command", bool(_which("codex")), _which("codex") or "missing"),
        Check("codex ChatGPT login", login_ok, login_detail),
        Check("handoff file", REVIEW_HANDOFF.exists(), str(REVIEW_HANDOFF)),
        Check("log file", AUTOPILOT_LOG.exists(), str(AUTOPILOT_LOG)),
    ]

    print("Joi Codex-only autopilot preflight")
    print(f"repo: {ROOT}")
    for check in checks:
        mark = "ok" if check.ok else "warn"
        print(f"[{mark}] {check.name}: {check.detail}")

    hard_fail_names = {"git repo", "branch", "codex command", "codex ChatGPT login", "handoff file", "log file"}
    if strict:
        hard_fail_names.update({"safe branch", "clean enough"})

    failed = [check for check in checks if not check.ok and check.name in hard_fail_names]
    if failed:
        print("preflight failed: " + ", ".join(check.name for check in failed))
        return 1
    return 0


def _append_log(message: str) -> None:
    timestamp = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")
    AUTOPILOT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUTOPILOT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"\n- {timestamp}: {message}\n")


def _base_prompt(role: str) -> str:
    return textwrap.dedent(
        f"""
        You are the Joi {role} inside a Codex-only autopilot loop.

        Repository:
        {ROOT}

        Shared mailbox:
        {REVIEW_HANDOFF}

        Audit log:
        {AUTOPILOT_LOG}

        Rules:
        - Use docs/REVIEW_HANDOFF.md as the source of truth.
        - Do not push, merge, create PRs, or edit secrets.
        - Do not delete files unless the task explicitly requires it.
        - Keep this to one small loop.
        - Stop if credentials, network, or elevated permissions are required.
        - Update docs/REVIEW_HANDOFF.md and docs/AUTOPILOT_LOG.md with concise results.
        - Keep voice/persona/private path leaks out of committed files.
        """
    ).strip()


def _role_prompt(role: str) -> str:
    specifics = {
        "Developer": "Implement only the Next Task. Run safe acceptance commands if possible. Update Worker Result.",
        "Tester": "Run the listed Acceptance Commands that are safe in this environment. Do not implement new scope. Update Worker Result with pass/fail details.",
        "Reviewer": "Review the current diff for bugs, safety regressions, missing tests, and scope creep. Update Reviewer Result with PASS or FAIL and findings.",
    }
    return f"{_base_prompt(role)}\n\nRole task:\n{specifics[role]}"


def _codex_exec(prompt: str, *, model: str, label: str) -> int:
    args = [
        "codex",
        "exec",
        "--json",
        "--model",
        model,
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
        "--cd",
        str(ROOT),
        "-",
    ]
    print(f"[joi-autopilot] starting {label}")
    result = _run(args, input_text=prompt)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        _append_log(f"{label} failed with exit code {result.returncode}")
        return result.returncode
    _append_log(f"{label} completed")
    return 0


def run_once(*, model: str) -> int:
    if preflight(strict=True) != 0:
        return 1
    _append_log("Codex-only autopilot run starting")
    for role in ("Developer", "Tester", "Reviewer"):
        code = _codex_exec(_role_prompt(role), model=model, label=role.lower())
        if code != 0:
            return code
    _append_log("Codex-only autopilot run finished")
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Joi Codex-only autopilot using ChatGPT-authenticated Codex CLI.")
    parser.add_argument("--preflight", action="store_true", help="Run local readiness checks and exit.")
    parser.add_argument("--strict", action="store_true", help="Treat unsafe branch or dirty worktree as preflight failures.")
    parser.add_argument("--run-once", action="store_true", help="Run one Developer -> Tester -> Reviewer loop with Codex CLI.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Codex model. Default: {DEFAULT_MODEL}")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.run_once:
        return run_once(model=args.model)
    return preflight(strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
