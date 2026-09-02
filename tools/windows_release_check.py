from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from tools.joi_doctor import build_doctor_report
    from tools.mvp_demo_check import build_mvp_demo_check_report
    from tools.package_windows_release import build_release_privacy_report, build_windows_release_package
    from tools.packaging_smoke import build_packaging_smoke_report
    from tools.provider_preflight import build_provider_preflight_report
    from tools.windows_setup_wizard import build_windows_setup_plan
except ModuleNotFoundError:
    from joi_doctor import build_doctor_report
    from mvp_demo_check import build_mvp_demo_check_report
    from package_windows_release import build_release_privacy_report, build_windows_release_package
    from packaging_smoke import build_packaging_smoke_report
    from provider_preflight import build_provider_preflight_report
    from windows_setup_wizard import build_windows_setup_plan


CHECK_VERSION = "joi.windows_release_check.v1"


def build_windows_release_check_report(
    workspace: Path | str | None = None,
    *,
    include_doctor: bool = True,
    port: int = 8765,
    allow_missing_exe: bool = False,
) -> dict[str, Any]:
    root = Path(workspace or Path(__file__).resolve().parents[1]).resolve()
    phases: list[dict[str, Any]] = []

    if include_doctor:
        doctor = build_doctor_report(root, port=port)
        phases.append(_phase("doctor", doctor, _counts_summary(doctor), required=False))

    setup = build_windows_setup_plan(root, apply=False)
    phases.append(_phase("windows_setup", setup, _counts_summary(setup), required=False))

    provider_preflight = build_provider_preflight_report(root)
    phases.append(_phase("provider_preflight", provider_preflight, _counts_summary(provider_preflight), required=False))

    demo_check = build_mvp_demo_check_report(root)
    phases.append(_phase("mvp_demo_check", demo_check, _counts_summary(demo_check), required=False))

    privacy = build_release_privacy_report()
    phases.append(
        {
            "name": "release_privacy_policy",
            "status": str(privacy.get("status", "fail")),
            "required": True,
            "summary": f"Protected {int(privacy.get('protected_sample_count', 0) or 0)} local-only sample paths.",
            "next_actions": ["Restore release privacy forbidden-path rules before release."] if privacy.get("status") != "ok" else [],
        }
    )

    smoke = build_packaging_smoke_report(root)
    phases.append(_phase("packaging_smoke", smoke, _counts_summary(smoke), required=True))

    package = build_windows_release_package(root, dry_run=True, require_exe=not allow_missing_exe)
    package_summary = f"Dry-run entries: {int(package.get('entry_count', 0) or 0)}; release shell: {'present' if package.get('includes_release_exe') else 'missing'}; Core runtime: {'present' if package.get('includes_core_sidecar') else 'missing'}."
    phases.append(_phase("release_package_dry_run", package, package_summary, required=True))

    status = _overall_status(phases)
    counts = {name: sum(1 for phase in phases if phase["status"] == name) for name in ("ok", "warn", "fail")}
    release_ready = status == "ok" and bool(package.get("includes_release_exe")) and bool(package.get("includes_core_sidecar"))
    return {
        "version": CHECK_VERSION,
        "safe_for_display": True,
        "workspace": ".",
        "status": status,
        "release_ready": release_ready,
        "counts": counts,
        "phases": phases,
        "next_actions": _next_actions(phases, allow_missing_exe=allow_missing_exe, includes_release_exe=bool(package.get("includes_release_exe"))),
    }


def windows_release_check_exit_code(report: dict[str, Any]) -> int:
    return 1 if report.get("status") == "fail" else 0


def print_text_report(report: dict[str, Any]) -> None:
    counts = report.get("counts", {})
    print(f"Joi Windows Release Check: {str(report.get('status', 'unknown')).upper()} ({counts.get('ok', 0)} ok, {counts.get('warn', 0)} warn, {counts.get('fail', 0)} fail)")
    print(f"release ready: {'yes' if report.get('release_ready') else 'no'}")
    for phase in report.get("phases", []):
        marker = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(phase.get("status"), "INFO")
        required = "required" if phase.get("required") else "advisory"
        print(f"[{marker}] {phase.get('name')} ({required}): {phase.get('summary')}")
    actions = report.get("next_actions") or []
    if actions:
        print("")
        print("Next actions:")
        for index, action in enumerate(actions, 1):
            print(f"{index}. {action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate safe Windows release readiness checks for Joi.")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--skip-doctor", action="store_true", help="Skip local machine readiness checks; useful in CI packaging jobs.")
    parser.add_argument("--allow-missing-exe", action="store_true", help="Allow dry-run packaging checks before the release shell is built.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_windows_release_check_report(
        Path(args.workspace),
        include_doctor=not args.skip_doctor,
        port=args.port,
        allow_missing_exe=args.allow_missing_exe,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)
    return windows_release_check_exit_code(report)


def _phase(name: str, report: dict[str, Any], summary: str, *, required: bool) -> dict[str, Any]:
    return {
        "name": name,
        "status": str(report.get("status", "fail")),
        "required": required,
        "summary": summary,
        "next_actions": list(report.get("next_actions") or []),
    }


def _counts_summary(report: dict[str, Any]) -> str:
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    return f"{int(counts.get('ok', 0) or 0)} ok, {int(counts.get('warn', 0) or 0)} warn, {int(counts.get('fail', 0) or 0)} fail."


def _overall_status(phases: list[dict[str, Any]]) -> str:
    required = [phase for phase in phases if phase.get("required")]
    if any(phase.get("status") == "fail" for phase in required):
        return "fail"
    if any(phase.get("status") == "warn" for phase in phases):
        return "warn"
    if any(phase.get("status") == "fail" for phase in phases):
        return "warn"
    return "ok"


def _next_actions(phases: list[dict[str, Any]], *, allow_missing_exe: bool, includes_release_exe: bool) -> list[str]:
    actions: list[str] = []
    if allow_missing_exe and not includes_release_exe:
        actions.append("Build the Tauri release shell before publishing the portable zip.")
    for phase in phases:
        for action in phase.get("next_actions") or []:
            if action and action not in actions:
                actions.append(str(action))
    return actions[:10]


if __name__ == "__main__":
    raise SystemExit(main())
