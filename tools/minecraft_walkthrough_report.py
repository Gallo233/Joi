"""Record a sanitized local result for one Minecraft real-server walkthrough scene.

The walkthrough is the slice's acceptance evidence, and it runs against the
user's own world -- so what a scene may leave behind is a name, a verdict, a
category and a short note. Coordinates, server addresses, world names, player
names, ids and logs are what this refuses to write down.

    .venv/bin/python tools/minecraft_walkthrough_report.py --init
    .venv/bin/python tools/minecraft_walkthrough_report.py --add combat_persona \
        --status pass --category ok --note "怕的时候先跑，等指令才动手"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data" / "local_visual_eval" / "minecraft_walkthrough.local.md"

# One scene per checklist section in docs/MINECRAFT_REAL_SERVER_WALKTHROUGH.md.
SCENES = {
    "join": "连接与进服",
    "observe": "状态与环境观察",
    "queries": "只读查询（配方/容器/方位）",
    "movement": "跟随与靠近",
    "gather": "采集与挖掘",
    "craft": "合成与熔炼",
    "storage": "容器、整理、装备与丢弃",
    "survival": "进食、钓鱼与睡觉",
    "build": "蓝图放置",
    "route_budget": "路径改方块与方块预算",
    "combat_persona": "战斗人格反应（方案 A）",
    "screen": "屏幕证据理解意图",
    "plan": "多步计划审批与逐步回报",
    "autonomy": "自主搭话与提案",
    "chat_channel": "游戏内聊天下指令",
    "memory": "跨会话世界记忆",
    "realtime_voice": "实时语音全链路",
    "recovery": "断线与超时恢复",
}
STATUSES = {"pass", "fail", "skip"}
CATEGORIES = {
    "ok",
    "action_refused",
    "action_unverified",
    "budget_wrong",
    "scope_wrong",
    "receipt_unclear",
    "persona_wrong",
    "language_wrong",
    "voice_leak",
    "coordinate_leak",
    "screen_evidence_unclear",
    "plan_wrong",
    "autonomy_noisy",
    "autonomy_silent",
    "memory_wrong",
    "latency_bad",
    "disconnect_mishandled",
    "runtime_error",
    "other",
}

_PRIVATE_PATTERNS = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"/(?:Users|home|private|tmp|var|Volumes)/", re.IGNORECASE),
    re.compile(r"https?://|www\.", re.IGNORECASE),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    re.compile(r":\d{4,5}\b"),
    # A coordinate is the one thing this whole slice keeps out of every channel.
    re.compile(r"-?\d+\s*[,，]\s*-?\d+\s*[,，]\s*-?\d+"),
    re.compile(r"\b[xyz]\s*[:=]\s*-?\d+", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:session|goal|approval|receipt|task)-[A-Za-z0-9_-]+\b", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{4,}\b"),
    re.compile(r"\.(?:png|jpg|jpeg|json|jsonl|log|txt|yaml|yml)\b", re.IGNORECASE),
)
_PRIVATE_TERMS = ("坐标", "端口", "地址", "存档名", "世界名", "服务器名", "账号", "密码", "token", "secret", "截图", "路径", "日志")


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a local-only sanitized Minecraft walkthrough result.")
    parser.add_argument("--init", action="store_true", help="Create the ignored local report template.")
    parser.add_argument("--add", choices=sorted(SCENES), help="Append one sanitized scene result.")
    parser.add_argument("--status", choices=sorted(STATUSES), help="Scene status: pass, fail, or skip.")
    parser.add_argument("--category", choices=sorted(CATEGORIES), help="Abstract outcome/failure category.")
    parser.add_argument("--note", default="", help="Short sanitized note. No coordinates, addresses, names, ids or logs.")
    parser.add_argument("--summary", action="store_true", help="Print which scenes still have no result.")
    args = parser.parse_args()

    if args.init:
        _write_template(overwrite=False)
        print("minecraft walkthrough: initialized")
        return 0

    if args.summary:
        recorded = _recorded_scenes()
        missing = [scene for scene in SCENES if scene not in recorded]
        print(f"minecraft walkthrough: {len(recorded)}/{len(SCENES)} scenes recorded")
        if missing:
            print("remaining: " + ", ".join(missing))
        return 0

    if args.add:
        if not args.status or not args.category:
            print("minecraft walkthrough: rejected")
            print("reason: status and category are required")
            return 2
        note = _sanitize_note(args.note)
        if note is None:
            print("minecraft walkthrough: rejected")
            print("reason: note contains private-looking details")
            return 2
        _append_entry(args.add, args.status, args.category, note)
        print("minecraft walkthrough: recorded")
        return 0

    parser.print_help()
    return 0


def _write_template(overwrite: bool = False) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if REPORT_PATH.exists() and not overwrite:
        return
    REPORT_PATH.write_text(
        "\n".join(
            [
                "# Joi Minecraft Real-Server Walkthrough (local only)",
                "",
                "Sanitized results only. See docs/MINECRAFT_REAL_SERVER_WALKTHROUGH.md.",
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


def _append_entry(scene: str, status: str, category: str, note: str) -> None:
    _write_template(overwrite=False)
    line = f"| {scene} | {status} | {category} | {note or '-'} |\n"
    with REPORT_PATH.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def _recorded_scenes() -> set[str]:
    if not REPORT_PATH.is_file():
        return set()
    recorded: set[str] = set()
    for line in REPORT_PATH.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        scene = line.split("|")[1].strip()
        if scene in SCENES:
            recorded.add(scene)
    return recorded


def _sanitize_note(note: str) -> str | None:
    value = " ".join((note or "").split()).strip()
    if len(value) > 160:
        value = value[:160].rstrip()
    if "|" in value:
        return None
    if any(term in value.lower() for term in _PRIVATE_TERMS):
        return None
    if any(pattern.search(value) for pattern in _PRIVATE_PATTERNS):
        return None
    return value


if __name__ == "__main__":
    raise SystemExit(main())
