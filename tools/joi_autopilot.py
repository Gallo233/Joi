#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
REVIEW_HANDOFF = DOCS / "REVIEW_HANDOFF.md"
AUTOPILOT_LOG = DOCS / "AUTOPILOT_LOG.md"
DEFAULT_MODEL = os.environ.get("JOI_AUTOPILOT_MODEL", "gpt-5.4")
SAFE_BRANCH_PREFIXES = ("codex/", "joi-autopilot/", "autopilot/")
CODEX_BIN_ENV_VARS = ("JOI_AUTOPILOT_CODEX_BIN", "AGENT_COMPANION_CODEX_BIN")
DOCS_ONLY_ALLOWED_PATHS = {
    "docs/AUTOPILOT_LOG.md",
    "docs/REVIEW_HANDOFF.md",
    "docs/AUTOPILOT_SANDBOX_TEST.md",
}
DOCS_ONLY_ALLOWED_PREFIXES = ("docs/AUTOPILOT_",)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class CodexCandidate:
    path: str
    source: str


@dataclass(frozen=True)
class CodexProbe:
    candidate: CodexCandidate
    version_ok: bool
    version_detail: str
    login_ok: bool
    login_detail: str

    @property
    def usable(self) -> bool:
        return self.version_ok and self.login_ok


def _run(
    args: Sequence[str],
    *,
    cwd: Path = ROOT,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = list(args)
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=dict(env) if env is not None else None,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 126, "", f"{exc.__class__.__name__}: {exc}")


def _git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return _run(["git", *args])


def _which(name: str) -> str:
    return shutil.which(name) or ""


def _default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _codex_env() -> dict[str, str]:
    env = os.environ.copy()
    codex_home = _default_codex_home()
    if codex_home.exists():
        env.setdefault("CODEX_HOME", str(codex_home))
    return env


def _dedupe_candidates(rows: list[CodexCandidate]) -> list[CodexCandidate]:
    seen: set[str] = set()
    result: list[CodexCandidate] = []
    for row in rows:
        key = str(Path(row.path).expanduser()).lower() if os.name == "nt" else str(Path(row.path).expanduser())
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _codex_candidates() -> list[CodexCandidate]:
    rows: list[CodexCandidate] = []
    for env_name in CODEX_BIN_ENV_VARS:
        value = os.environ.get(env_name, "").strip()
        if value:
            rows.append(CodexCandidate(value, env_name))

    codex_home = _default_codex_home()
    sandbox_bin = codex_home / ".sandbox-bin"
    for name in ("codex.exe", "codex.cmd", "codex"):
        candidate = sandbox_bin / name
        if candidate.is_file():
            rows.append(CodexCandidate(str(candidate), "codex sandbox bin"))

    which_codex = _which("codex")
    if which_codex:
        rows.append(CodexCandidate(which_codex, "PATH"))

    if os.name == "nt":
        where_result = _run(["where.exe", "codex"])
        if where_result.returncode == 0:
            for line in where_result.stdout.splitlines():
                path = line.strip()
                if path:
                    rows.append(CodexCandidate(path, "where.exe"))

    return _dedupe_candidates(rows)


def _current_branch() -> str:
    result = _git(["branch", "--show-current"])
    return result.stdout.strip() if result.returncode == 0 else ""


def _is_git_repo() -> bool:
    result = _git(["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def _auth_file_summary() -> str:
    auth_path = _default_codex_home() / "auth.json"
    if not auth_path.exists():
        return "auth file missing"
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception:
        return "auth file unreadable"
    if not isinstance(payload, dict):
        return "auth file invalid"
    mode = str(payload.get("auth_mode") or "").strip().lower()
    has_api_key = bool(payload.get("OPENAI_API_KEY"))
    tokens = payload.get("tokens")
    has_chatgpt_tokens = isinstance(tokens, dict) and bool(tokens.get("access_token") and tokens.get("refresh_token"))
    if mode == "chatgpt" and has_chatgpt_tokens:
        return "ChatGPT auth file present"
    if mode == "apikey" or has_api_key:
        return "API-key auth file detected"
    return f"auth file mode: {mode or 'unknown'}"


def _redact_probe_text(text: str) -> str:
    result = text
    for value in {str(Path.home()), os.environ.get("USERPROFILE", ""), str(ROOT)}:
        if value:
            result = result.replace(value, "~")
            result = result.replace(value.replace("\\", "\\\\"), "~")
    result = re.sub(r"\\\\\?\\[A-Za-z]:\\[^\s;]+", "~", result)
    return result.strip()


def _display_path(path: str) -> str:
    return _redact_probe_text(path)


def _probe_output(result: subprocess.CompletedProcess[str], *, success_fallback: str, failure_fallback: str) -> str:
    raw_lines = [line.strip() for part in (result.stdout, result.stderr) for line in part.splitlines() if line.strip()]
    primary = [line for line in raw_lines if not line.lower().startswith("warning:")]
    lines = primary or raw_lines
    if not lines:
        return success_fallback if result.returncode == 0 else failure_fallback
    return _redact_probe_text("; ".join(lines[:2]))


def _parse_codex_login_output(result: subprocess.CompletedProcess[str]) -> tuple[bool, str]:
    output = _probe_output(result, success_fallback="logged in", failure_fallback="login status failed")
    if result.returncode != 0:
        return False, output or "login status failed"
    if "Logged in using ChatGPT" in output:
        return True, "ChatGPT subscription login"
    if "Logged in using API" in output or "API key" in output:
        return False, "API-key login detected; use ChatGPT login to avoid API billing"
    if "Not logged in" in output:
        return False, "not logged in"
    return True, output or "logged in"


def _probe_codex_candidate(candidate: CodexCandidate) -> CodexProbe:
    env = _codex_env()
    version_result = _run([candidate.path, "--version"], env=env)
    version_ok = version_result.returncode == 0
    version_output = _probe_output(version_result, success_fallback="version probe ok", failure_fallback="version probe failed")
    if not version_ok:
        return CodexProbe(candidate, False, version_output, False, "login not checked")

    login_result = _run([candidate.path, "login", "status"], env=env)
    login_ok, login_detail = _parse_codex_login_output(login_result)
    return CodexProbe(candidate, True, version_output, login_ok, login_detail)


def _codex_probes() -> list[CodexProbe]:
    return [_probe_codex_candidate(candidate) for candidate in _codex_candidates()]


def _selected_codex_probe() -> CodexProbe | None:
    probes = _codex_probes()
    for probe in probes:
        if probe.usable:
            return probe
    return probes[0] if probes else None


def _codex_command_status(probes: list[CodexProbe]) -> tuple[bool, str]:
    if not probes:
        return False, "codex missing"
    usable = [probe for probe in probes if probe.usable]
    if usable:
        probe = usable[0]
        return True, f"{_display_path(probe.candidate.path)} ({probe.version_detail}; {probe.login_detail})"
    accessible = [probe for probe in probes if probe.version_ok]
    if accessible:
        probe = accessible[0]
        return True, f"{_display_path(probe.candidate.path)} ({probe.version_detail}; {probe.login_detail})"
    details = "; ".join(f"{_display_path(probe.candidate.path)}: {probe.version_detail}" for probe in probes[:3])
    return False, details or "codex inaccessible"


def _codex_login_status(probes: list[CodexProbe]) -> tuple[bool, str]:
    if not probes:
        return False, "codex missing"
    usable = [probe for probe in probes if probe.usable]
    if usable:
        probe = usable[0]
        return True, f"{probe.login_detail} via {probe.candidate.source}"
    login_details = "; ".join(f"{probe.candidate.source}: {probe.login_detail}" for probe in probes[:3])
    auth_summary = _auth_file_summary()
    if auth_summary:
        login_details = f"{login_details}; {auth_summary}" if login_details else auth_summary
    return False, login_details or "login status failed"


def _codex_exec_help(path: str) -> str:
    result = _run([path, "exec", "--help"], env=_codex_env())
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def _codex_exec_interface_status(probe: CodexProbe | None) -> tuple[bool, str]:
    if probe is None or not probe.usable:
        return False, "codex not usable"
    help_text = _codex_exec_help(probe.candidate.path)
    required = ("--json", "--model", "--sandbox", "--cd")
    missing = [flag for flag in required if flag not in help_text]
    if missing:
        return False, "missing exec flags: " + ", ".join(missing)
    if "--ask-for-approval" in help_text:
        return True, "exec supports approval policy flag"
    if "-c, --config" in help_text or "--config" in help_text:
        return True, "exec supports approval policy config override"
    return False, "exec cannot force approval_policy=never"


def _codex_exec_args(path: str, *, model: str) -> list[str]:
    help_text = _codex_exec_help(path)
    args = [
        path,
        "exec",
        "--json",
        "--model",
        model,
        "--sandbox",
        "workspace-write",
    ]
    if "--ask-for-approval" in help_text:
        args.extend(["--ask-for-approval", "never"])
    else:
        args.extend(["-c", "approval_policy=never"])
    args.extend(
        [
            "--cd",
            str(ROOT),
            "-",
        ]
    )
    return args


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


def _changed_paths_since(base_head: str) -> set[str]:
    paths: set[str] = set()
    commands = [
        ["diff", "--name-only", f"{base_head}..HEAD"],
        ["diff", "--name-only"],
        ["diff", "--name-only", "--cached"],
    ]
    for command in commands:
        result = _git(command)
        if result.returncode != 0:
            paths.add("<git diff unavailable>")
            continue
        for line in result.stdout.splitlines():
            path = line.strip().replace("\\", "/")
            if path:
                paths.add(path)

    status = _git(["status", "--short"])
    if status.returncode != 0:
        paths.add("<git status unavailable>")
        return paths
    for line in status.stdout.splitlines():
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().replace("\\", "/")
        if path:
            paths.add(path)
    return paths


def _docs_only_allowed(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized in DOCS_ONLY_ALLOWED_PATHS or any(normalized.startswith(prefix) for prefix in DOCS_ONLY_ALLOWED_PREFIXES)


def _docs_only_scope_violations(base_head: str) -> list[str]:
    paths = sorted(_changed_paths_since(base_head))
    return [path for path in paths if not _docs_only_allowed(path)]


def _check_docs_only_scope(base_head: str, label: str) -> bool:
    violations = _docs_only_scope_violations(base_head)
    if not violations:
        return True
    preview = ", ".join(violations[:8])
    if len(violations) > 8:
        preview += f", ... (+{len(violations) - 8} more)"
    message = f"{label} stopped: docs-only scope violation: {preview}"
    print(f"[joi-autopilot] {message}", file=sys.stderr)
    _append_log(message)
    return False


def preflight(*, strict: bool = False) -> int:
    branch = _current_branch()
    codex_probes = _codex_probes()
    selected_codex = next((probe for probe in codex_probes if probe.usable), None)
    codex_command_ok, codex_command_detail = _codex_command_status(codex_probes)
    login_ok, login_detail = _codex_login_status(codex_probes)
    codex_exec_ok, codex_exec_detail = _codex_exec_interface_status(selected_codex)
    dirty = _has_unexpected_dirty_entries()
    checks = [
        Check("git repo", _is_git_repo(), str(ROOT)),
        Check("branch", bool(branch), branch or "unknown"),
        Check("safe branch", branch != "main" and branch.startswith(SAFE_BRANCH_PREFIXES), branch or "unknown"),
        Check("clean enough", not dirty, "no unexpected dirty code" if not dirty else "unexpected dirty worktree entries"),
        Check("codex command", codex_command_ok, codex_command_detail),
        Check("codex ChatGPT login", login_ok, login_detail),
        Check("codex exec interface", codex_exec_ok, codex_exec_detail),
        Check("handoff file", REVIEW_HANDOFF.exists(), str(REVIEW_HANDOFF)),
        Check("log file", AUTOPILOT_LOG.exists(), str(AUTOPILOT_LOG)),
    ]

    print("Joi Codex-only autopilot preflight")
    print(f"repo: {ROOT}")
    for check in checks:
        mark = "ok" if check.ok else "warn"
        print(f"[{mark}] {check.name}: {check.detail}")

    hard_fail_names = {"git repo", "branch", "codex command", "codex ChatGPT login", "codex exec interface", "handoff file", "log file"}
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
        - P0 smoke runs are docs-only: do not edit application code, tests, fixtures, tools other than this autopilot runner, or non-autopilot docs.
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
    probe = _selected_codex_probe()
    if probe is None or not probe.usable:
        _append_log(f"{label} failed because Codex CLI is not accessible with ChatGPT login")
        print("[joi-autopilot] Codex CLI is not accessible with ChatGPT login", file=sys.stderr)
        return 1
    args = _codex_exec_args(probe.candidate.path, model=model)
    print(f"[joi-autopilot] starting {label}")
    result = _run(args, input_text=prompt, env=_codex_env())
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
    base = _git(["rev-parse", "HEAD"])
    if base.returncode != 0:
        print("[joi-autopilot] failed to read base HEAD", file=sys.stderr)
        return 1
    base_head = base.stdout.strip()
    _append_log("Codex-only autopilot run starting")
    for role in ("Developer", "Tester", "Reviewer"):
        code = _codex_exec(_role_prompt(role), model=model, label=role.lower())
        if not _check_docs_only_scope(base_head, role.lower()):
            return 1
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
