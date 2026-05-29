from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_companion.core.skill_manifest import build_native_skill_manifest
from agent_companion.core.speech_input import AsrRuntimeState, build_asr_provider
from agent_companion.core.tts_bridge import TtsBridge
from tools.provider_preflight import build_provider_preflight_report


DEMO_CHECK_VERSION = "joi.mvp_demo_check.v1"

DEMO_SPECS = (
    {
        "id": "watch_together",
        "label": "Watch Together",
        "goal": "Verify visible-page/video watching, follow-up recall, and safe voice wording.",
        "prompts": ["陪我看当前页面", "你看到了什么？", "这里有什么按钮？"],
        "required_skills": ("joi.watch", "joi.computer_use"),
        "recommended_providers": ("fast", "vision", "ocr"),
        "required_boundaries": ("session_window", "audited_session"),
    },
    {
        "id": "coding_task",
        "label": "Codex Coding",
        "goal": "Verify coding-task handoff, sanitized task card, and permission fail-closed behavior.",
        "prompts": ["帮我检查这个项目的发布准备状态并只汇报结果"],
        "required_skills": ("joi.codex",),
        "recommended_providers": ("fast", "code", "summarize"),
        "required_boundaries": ("workspace_audit",),
    },
    {
        "id": "game_skill",
        "label": "OK-WW Game Skill",
        "goal": "Verify game-skill dry-run, approval boundary, and non-overclaiming status copy.",
        "prompts": ["帮我刷鸣潮日常，先只做 dry-run 检查"],
        "required_skills": ("joi.ok_ww",),
        "recommended_providers": ("fast",),
        "required_boundaries": ("external_game_runner",),
    },
)


def build_mvp_demo_check_report(workspace: Path | str | None = None) -> dict[str, Any]:
    root = Path(workspace or ROOT).resolve()
    provider_report = build_provider_preflight_report(root)
    provider_rows = {row.get("name"): row for row in provider_report.get("providers", []) if isinstance(row, dict)}
    skill_rows = {row.get("id"): row for row in _skill_rows(root) if isinstance(row, dict)}
    demos = [_demo_row(spec, skill_rows, provider_rows) for spec in DEMO_SPECS]
    status = _overall_status(demos)
    counts = {name: sum(1 for demo in demos if demo["status"] == name) for name in ("ok", "warn", "fail")}
    return {
        "version": DEMO_CHECK_VERSION,
        "safe_for_display": True,
        "workspace": ".",
        "status": status,
        "counts": counts,
        "demos": demos,
        "next_actions": _next_actions(demos),
    }


def mvp_demo_check_exit_code(report: dict[str, Any]) -> int:
    return 1 if report.get("status") == "fail" else 0


def print_text_report(report: dict[str, Any]) -> None:
    counts = report.get("counts", {})
    print(f"Joi MVP Demo Check: {str(report.get('status', 'unknown')).upper()} ({counts.get('ok', 0)} ok, {counts.get('warn', 0)} warn, {counts.get('fail', 0)} fail)")
    for demo in report.get("demos", []):
        marker = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(demo.get("status"), "INFO")
        print(f"[{marker}] {demo.get('id')}: {demo.get('summary')}")
        for prompt in demo.get("prompts", []):
            print(f"       prompt: {prompt}")
        if demo.get("action"):
            print(f"       next: {demo.get('action')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check safe readiness for Joi's three MVP demo loops.")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_mvp_demo_check_report(Path(args.workspace))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)
    return mvp_demo_check_exit_code(report)


def _skill_rows(root: Path) -> list[dict[str, Any]]:
    asr_state = _safe_asr_state(root)
    tts_status = _safe_tts_status(root)
    manifest = build_native_skill_manifest(root, asr_state=asr_state, tts_status=tts_status)
    return [row for row in manifest.get("skills", []) if isinstance(row, dict)]


def _safe_asr_state(root: Path) -> AsrRuntimeState:
    try:
        _, state = build_asr_provider(root)
        return state
    except Exception:
        return AsrRuntimeState(False, False, "none", error="asr_config_error")


def _safe_tts_status(root: Path) -> dict[str, Any]:
    try:
        bridge = TtsBridge(root)
        try:
            return bridge.status_payload()
        finally:
            bridge.shutdown()
    except Exception:
        return {"enabled": False, "configured": False, "provider": "none", "last_error": "tts_config_error"}


def _demo_row(spec: dict[str, Any], skill_rows: dict[str, dict[str, Any]], provider_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    skill_issues: list[str] = []
    boundary_issues: list[str] = []
    for skill_id in spec["required_skills"]:
        row = skill_rows.get(skill_id)
        if not row:
            skill_issues.append(f"{skill_id}:missing")
            continue
        if not row.get("enabled", True):
            skill_issues.append(f"{skill_id}:disabled")
        capability = str(row.get("local_capability") or "unavailable")
        if capability not in {"ready", "off"}:
            skill_issues.append(f"{skill_id}:{capability}")
        if skill_id in {"joi.codex", "joi.ok_ww"} and capability != "ready":
            skill_issues.append(f"{skill_id}:not_ready")
    for boundary in spec["required_boundaries"]:
        if not any(boundary == str(row.get("state_policy") or "") for row in skill_rows.values()):
            boundary_issues.append(boundary)
    provider_warnings = [
        name
        for name in spec["recommended_providers"]
        if str(provider_rows.get(name, {}).get("state") or "unavailable") not in {"ready", "mock"}
    ]
    if boundary_issues:
        status = "fail"
        summary = f"Missing safety boundary: {', '.join(boundary_issues)}."
        action = "Restore native skill manifests before using MVP demo scripts."
    elif skill_issues:
        status = "warn"
        summary = f"{spec['label']} needs local setup: {', '.join(sorted(set(skill_issues)))}."
        action = _action_for_demo(str(spec["id"]))
    elif provider_warnings:
        status = "warn"
        summary = f"{spec['label']} can run, but recommended providers need setup: {', '.join(provider_warnings)}."
        action = "Run tools\\provider_preflight.py and configure optional demo providers as needed."
    else:
        status = "ok"
        summary = f"{spec['label']} demo loop is ready."
        action = ""
    return {
        "id": spec["id"],
        "label": spec["label"],
        "status": status,
        "goal": spec["goal"],
        "summary": summary,
        "prompts": list(spec["prompts"]),
        "required_skills": list(spec["required_skills"]),
        "recommended_providers": list(spec["recommended_providers"]),
        "privacy_boundary": "Do not record screenshots, OCR text, URLs, account names, local paths, logs, task ids, approval ids, or exact private titles.",
        "action": action,
    }


def _action_for_demo(demo_id: str) -> str:
    return {
        "watch_together": "Run provider preflight and open a harmless public page or paused video before testing Watch Together.",
        "coding_task": "Install and sign in to Codex CLI, or set AGENT_COMPANION_CODEX_BIN to a valid executable.",
        "game_skill": "Set OK_WW_RUNNER to the local OK-WW runner before testing the game demo.",
    }.get(demo_id, "Prepare the local demo dependency and rerun the demo check.")


def _overall_status(demos: list[dict[str, Any]]) -> str:
    if any(demo["status"] == "fail" for demo in demos):
        return "fail"
    if any(demo["status"] == "warn" for demo in demos):
        return "warn"
    return "ok"


def _next_actions(demos: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for status in ("fail", "warn"):
        for demo in demos:
            action = str(demo.get("action") or "")
            if demo.get("status") == status and action and action not in actions:
                actions.append(action)
    return actions[:8]


if __name__ == "__main__":
    raise SystemExit(main())
