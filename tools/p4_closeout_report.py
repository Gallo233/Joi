from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_RELATIVE = Path("data") / "local_visual_eval" / "p4_closeout_report.local.md"
REPORT_PATH = ROOT / REPORT_RELATIVE


def report_path(root: Path | None = None) -> Path:
    """Where the local report lives under `root`, the checkout by default.

    The path used to be fixed to the checkout, so the regression suite -- which
    runs this tool as a subprocess -- wrote into the developer's own data tree
    however it was invoked.
    """

    return (root or ROOT) / REPORT_RELATIVE

SCENES = {
    "browser_click": "浏览器按钮",
    "watch_page_video": "陪看网页/视频",
    "canvas_video_controls": "Canvas/视频控件",
    "game_hud": "游戏/HUD",
}
STATUSES = {"pass", "fail", "skip"}
CATEGORIES = {
    "ok",
    "target_not_found",
    "evidence_unclear",
    "selection_confusing",
    "approval_confusing",
    "voice_leak",
    "visual_only_unclear",
    "uia_mismatch",
    "capture_rect_untrusted",
    "overlay_misaligned",
    "verification_unclear",
    "ok_ww_unavailable",
    "asr_tts_issue",
    "runtime_error",
    "other",
}

_PRIVATE_PATTERNS = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"/(?:Users|home|private|tmp|var|Volumes)/", re.IGNORECASE),
    re.compile(r"https?://|www\.", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:task|approval|selection)-[A-Za-z0-9_-]+\b", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{4,}\b"),
    re.compile(r"\btoken\b|\bsecret\b|\bpassword\b", re.IGNORECASE),
    re.compile(r"\.(?:png|ppm|jpg|jpeg|webp|gif|bmp|json|jsonl|log|txt|yaml|yml)\b", re.IGNORECASE),
)
_PRIVATE_TERMS = ("账号", "密码", "验证码", "手机号", "身份证", "窗口标题", "截图", "路径", "URL", "网址", "OCR")


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a local-only sanitized Joi P4 closeout report.")
    parser.add_argument("--init", action="store_true", help="Create the ignored local report template.")
    parser.add_argument("--add", choices=sorted(SCENES), help="Append one sanitized closeout entry.")
    parser.add_argument("--status", choices=sorted(STATUSES), help="Entry status: pass, fail, or skip.")
    parser.add_argument("--category", choices=sorted(CATEGORIES), help="Abstract outcome/failure category.")
    parser.add_argument("--note", default="", help="Short sanitized note. Do not include screenshots, URLs, paths, OCR text, account data, or ids.")
    parser.add_argument("--root", type=Path, default=None, help="Write under this directory instead of the checkout.")
    args = parser.parse_args()

    if args.init:
        _write_template(overwrite=False, root=args.root)
        print("p4 closeout report: initialized")
        return 0

    if args.add:
        if not args.status or not args.category:
            print("p4 closeout report: rejected")
            print("reason: status and category are required")
            return 2
        note = _sanitize_note(args.note)
        if note is None:
            print("p4 closeout report: rejected")
            print("reason: note contains private-looking details")
            return 2
        _append_entry(args.add, args.status, args.category, note, root=args.root)
        print("p4 closeout report: recorded")
        return 0

    parser.print_help()
    return 0


def _write_template(overwrite: bool = False, root: Path | None = None) -> None:
    destination = report_path(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        return
    destination.write_text(
        "\n".join(
            [
                "# Joi P4 Closeout Local Report",
                "",
                "## Entries",
                "",
                "| scene | status | category | note |",
                "| --- | --- | --- | --- |",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _append_entry(scene: str, status: str, category: str, note: str, root: Path | None = None) -> None:
    _write_template(overwrite=False, root=root)
    line = f"| {scene} | {status} | {category} | {note or '-'} |\n"
    with report_path(root).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def _sanitize_note(note: str) -> str | None:
    value = " ".join((note or "").split()).strip()
    if len(value) > 160:
        value = value[:160].rstrip()
    if "|" in value:
        return None
    if any(term in value for term in _PRIVATE_TERMS):
        return None
    if any(pattern.search(value) for pattern in _PRIVATE_PATTERNS):
        return None
    return value


if __name__ == "__main__":
    raise SystemExit(main())
