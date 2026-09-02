from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from agent_companion.core.ok_ww import ok_ww_runner_path, ok_ww_setup_hint
from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class OkWwTool(ToolAdapter):
    name = "game.ok_ww.run"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.run_dir = workspace / "data" / "agent_companion" / "game_runs"

    def run(self, request: ToolRequest) -> ToolResult:
        intent = str(request.arguments.get("intent") or "").strip()
        dry_run = bool(request.arguments.get("dry_run", True))
        script = ok_ww_runner_path()
        if dry_run:
            return ToolResult(
                ok=True,
                agent_state={"tool": self.name, "intent": intent, "dry_run": True, "script_exists": script is not None},
                display_card=DisplayCard(
                    "游戏技能",
                    "游戏自动化已检查，可以等待确认。" if script else "游戏自动化还没有配置好。",
                    f"意图：{intent}\n" + ("runner 已就绪。" if script else ok_ww_setup_hint()),
                ),
                voice_line=safe_voice_line("我先做了游戏任务规划，启动前需要你确认。", sprite="3"),
            )
        if script is None:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": "runner_not_configured", "setup_hint": ok_ww_setup_hint()},
                display_card=DisplayCard("游戏技能", "没有找到 OK-WW runner。", ok_ww_setup_hint(), status="failed"),
                voice_line=safe_voice_line("游戏技能还没接好。", sprite="4"),
            )
        self.run_dir.mkdir(parents=True, exist_ok=True)
        stamp = str(int(time.time() * 1000))
        stdout_path = self.run_dir / f"{stamp}.stdout.log"
        stderr_path = self.run_dir / f"{stamp}.stderr.log"
        timeout = int(os.environ.get("AGENT_COMPANION_OK_WW_TIMEOUT", "3600"))
        started = time.time()
        try:
            with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
                result = subprocess.run(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Intent", intent, "-ExitWhenDone"],
                    cwd=str(self.workspace),
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                    timeout=timeout,
                )
        except subprocess.TimeoutExpired:
            artifacts = [self._rel(stdout_path), self._rel(stderr_path)]
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": "timeout", "intent": intent, "artifacts": artifacts},
                display_card=DisplayCard("游戏技能", "OK-WW 执行超时。", status="failed", artifacts=artifacts),
                voice_line=safe_voice_line("游戏技能执行太久了，我先停下汇报。", sprite="4"),
            )
        elapsed = time.time() - started
        output = "\n".join(
            part
            for part in (
                stdout_path.read_text(encoding="utf-8", errors="replace").strip(),
                stderr_path.read_text(encoding="utf-8", errors="replace").strip(),
            )
            if part
        )
        artifacts = [self._rel(stdout_path), self._rel(stderr_path)]
        return ToolResult(
            ok=result.returncode == 0,
            agent_state={
                "tool": self.name,
                "returncode": result.returncode,
                "intent": intent,
                "elapsed_seconds": elapsed,
                "artifacts": artifacts,
            },
            display_card=DisplayCard(
                "游戏技能",
                "游戏自动化已接收任务。" if result.returncode == 0 else "游戏自动化执行失败。",
                output[-3000:] or ("外部技能返回成功，但没有输出详情。" if result.returncode == 0 else ""),
                status="success" if result.returncode == 0 else "failed",
                artifacts=artifacts,
            ),
            voice_line=safe_voice_line("游戏技能已经接手了。" if result.returncode == 0 else "游戏技能没有启动成功。", sprite="5" if result.returncode == 0 else "4"),
        )

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return str(path)
