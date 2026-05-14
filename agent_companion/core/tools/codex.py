from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class CodexTool(ToolAdapter):
    name = "codex.run"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.run_dir = workspace / "data" / "agent_companion" / "codex_runs"

    def run(self, request: ToolRequest) -> ToolResult:
        goal = str(request.arguments.get("goal") or "").strip()
        if not goal:
            return self._failed("Codex 任务目标为空。", {"error": "empty_goal"})
        codex = self._codex_executable()
        if not codex:
            return self._failed("没有找到本地 Codex CLI。", {"error": "codex_not_found", "goal": goal})

        self.run_dir.mkdir(parents=True, exist_ok=True)
        stamp = str(int(time.time() * 1000))
        stdout_path = self.run_dir / f"{stamp}.stdout.jsonl"
        stderr_path = self.run_dir / f"{stamp}.stderr.log"
        final_path = self.run_dir / f"{stamp}.final.txt"
        command = [
            codex,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--cd",
            str(self.workspace),
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            "--output-last-message",
            str(final_path),
            goal,
        ]
        started = time.time()
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            result = subprocess.run(command, cwd=str(self.workspace), stdout=stdout, stderr=stderr, text=True)
        elapsed = time.time() - started
        final = final_path.read_text(encoding="utf-8", errors="replace").strip() if final_path.is_file() else ""
        stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace").strip()[-1600:] if stderr_path.is_file() else ""
        ok = result.returncode == 0
        body = "\n".join(part for part in (final, stderr_tail if not ok else "") if part)
        return ToolResult(
            ok=ok,
            agent_state={
                "tool": self.name,
                "goal": goal,
                "returncode": result.returncode,
                "elapsed_seconds": elapsed,
                "artifacts": [self._rel(stdout_path), self._rel(stderr_path), self._rel(final_path)],
            },
            display_card=DisplayCard(
                "Codex 任务",
                "Codex 已完成。" if ok else f"Codex 失败，退出码 {result.returncode}。",
                body or "没有最终摘要。",
                status="success" if ok else "failed",
                artifacts=[self._rel(stdout_path), self._rel(stderr_path), self._rel(final_path)],
            ),
            voice_line=safe_voice_line("写码任务完成了。" if ok else "写码任务没有跑通，细节在卡片里。", sprite="5" if ok else "4"),
        )

    def _failed(self, message: str, state: dict) -> ToolResult:
        return ToolResult(
            ok=False,
            agent_state={"tool": self.name, **state},
            display_card=DisplayCard("Codex 任务", message, status="failed"),
            voice_line=safe_voice_line("写码能力还没有准备好。", sprite="4"),
        )

    @staticmethod
    def _codex_executable() -> str:
        override = os.environ.get("AGENT_COMPANION_CODEX_BIN", "").strip()
        if override:
            return override if Path(override).is_file() else ""
        try:
            probe = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=5)
            if probe.returncode == 0:
                return "codex"
        except Exception:
            pass
        candidate = shutil.which("codex") or ""
        return candidate

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return str(path)
