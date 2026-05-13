from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class OkWwTool(ToolAdapter):
    name = "game.ok_ww.run"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def run(self, request: ToolRequest) -> ToolResult:
        intent = str(request.arguments.get("intent") or "").strip()
        dry_run = bool(request.arguments.get("dry_run", True))
        script = Path(os.environ.get("OK_WW_RUNNER", r"C:\Users\liujialuo\.codex\skills\github_issue_solver\scripts\run_ok_ww.ps1"))
        if dry_run:
            return ToolResult(
                ok=True,
                agent_state={"tool": self.name, "intent": intent, "dry_run": True, "script_exists": script.is_file()},
                display_card=DisplayCard("游戏技能", "已完成 OK-WW dry-run 规划。", f"意图：{intent}\n脚本存在：{script.is_file()}"),
                voice_line=safe_voice_line("我先做了游戏任务规划，启动前需要你确认。", sprite="3"),
            )
        if not script.is_file():
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": "runner_not_found"},
                display_card=DisplayCard("游戏技能", "没有找到 OK-WW runner。", status="failed"),
                voice_line=safe_voice_line("游戏技能还没接好。", sprite="4"),
            )
        result = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Intent", intent, "-ExitWhenDone"],
            cwd=str(self.workspace),
            capture_output=True,
            text=True,
            timeout=20,
        )
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        return ToolResult(
            ok=result.returncode == 0,
            agent_state={"tool": self.name, "returncode": result.returncode, "intent": intent},
            display_card=DisplayCard("游戏技能", "OK-WW 已启动。" if result.returncode == 0 else "OK-WW 启动失败。", output[-2000:], status="success" if result.returncode == 0 else "failed"),
            voice_line=safe_voice_line("游戏技能已经启动。" if result.returncode == 0 else "游戏技能没有启动成功。", sprite="5" if result.returncode == 0 else "4"),
        )

