from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Any


SETUP_VERSION = "joi.windows_setup_wizard.v1"


def build_windows_setup_plan(workspace: Path | str | None = None, *, apply: bool = False) -> dict[str, Any]:
    root = Path(workspace or Path(__file__).resolve().parents[1]).resolve()
    example_path = root / "config.example.yaml"
    config_path = root / "config.yaml"
    steps: list[dict[str, str]] = []
    created: list[str] = []

    def add(status: str, name: str, summary: str, action: str = "") -> None:
        steps.append({"status": status, "name": name, "summary": summary, "action": action})

    if example_path.is_file():
        add("ok", "config_template", "Found config.example.yaml.")
    else:
        add("fail", "config_template", "Missing config.example.yaml.", "Restore config.example.yaml before first-run setup.")

    if config_path.is_file():
        add("ok", "local_config", "config.yaml already exists; it was not modified.")
    elif example_path.is_file() and apply:
        shutil.copyfile(example_path, config_path)
        created.append("config.yaml")
        add("ok", "local_config", "Created config.yaml from config.example.yaml.")
    elif example_path.is_file():
        add("warn", "local_config", "config.yaml is missing; dry-run would create it from config.example.yaml.", "Run: .venv\\Scripts\\python.exe tools\\windows_setup_wizard.py --apply")
    else:
        add("fail", "local_config", "Cannot create config.yaml without config.example.yaml.", "Restore config.example.yaml, then rerun setup.")

    add("ok", "secret_policy", "Secrets stay outside setup output and should use environment variables or secrets.yaml.")
    add("ok", "python_setup", "Python setup command is ready.", "Run: py -3 -m venv .venv; .venv\\Scripts\\python.exe -m pip install -r requirements.txt")
    add("ok", "doctor_check", "Doctor command is ready.", "Run: .venv\\Scripts\\python.exe tools\\joi_doctor.py")
    add("ok", "provider_preflight", "Provider preflight command is ready.", "Run: .venv\\Scripts\\python.exe tools\\provider_preflight.py")
    add("ok", "mvp_demo_check", "MVP demo readiness command is ready.", "Run: .venv\\Scripts\\python.exe tools\\mvp_demo_check.py")
    add("ok", "launch", "Launch command is ready.", "Run: start_joi.bat")

    counts = {status: sum(1 for step in steps if step["status"] == status) for status in ("ok", "warn", "fail")}
    status = "fail" if counts["fail"] else "warn" if counts["warn"] else "ok"
    return {
        "version": SETUP_VERSION,
        "safe_for_display": True,
        "workspace": ".",
        "applied": bool(apply),
        "status": status,
        "counts": counts,
        "created": created,
        "steps": steps,
        "next_actions": _next_actions(steps),
    }


def windows_setup_exit_code(report: dict[str, Any]) -> int:
    return 1 if report.get("status") == "fail" else 0


def print_text_report(report: dict[str, Any]) -> None:
    counts = report.get("counts", {})
    print(f"Joi Windows Setup Wizard: {str(report.get('status', 'unknown')).upper()} ({counts.get('ok', 0)} ok, {counts.get('warn', 0)} warn, {counts.get('fail', 0)} fail)")
    if report.get("created"):
        print(f"created: {', '.join(str(item) for item in report.get('created', []))}")
    for step in report.get("steps", []):
        marker = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(step.get("status"), "INFO")
        print(f"[{marker}] {step.get('name')}: {step.get('summary')}")
        if step.get("action"):
            print(f"       next: {step.get('action')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a safe Windows first-run setup wizard for Joi.")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--apply", action="store_true", help="Create config.yaml from config.example.yaml when it is missing. Secrets are never written.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_windows_setup_plan(Path(args.workspace), apply=args.apply)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)
    return windows_setup_exit_code(report)


def _next_actions(steps: list[dict[str, str]]) -> list[str]:
    actions: list[str] = []
    for status in ("fail", "warn", "ok"):
        for step in steps:
            action = step.get("action", "")
            if step.get("status") == status and action and action not in actions:
                actions.append(action)
    return actions[:8]


if __name__ == "__main__":
    raise SystemExit(main())
