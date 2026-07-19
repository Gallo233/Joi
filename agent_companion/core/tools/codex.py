from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from agent_companion.core.codex_events import (
    PERMISSION_FAIL_CLOSED_MESSAGE,
    build_codex_run_state,
    codex_card_body,
    codex_permission_approval_arguments,
)
from agent_companion.core.codex_support import codex_executable
from agent_companion.core.schemas import DisplayCard, RiskLevel, ToolRequest, ToolResult
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
            return self._failed("Codex 任务目标为空。", {"error": "empty_goal"}, status="failed")
        codex = self._codex_executable()
        if not codex:
            return self._failed("没有找到本地 Codex CLI。", {"error": "codex_not_found"}, status="not_found")

        self.run_dir.mkdir(parents=True, exist_ok=True)
        stamp = str(int(time.time() * 1000))
        stdout_path = self.run_dir / f"{stamp}.stdout.jsonl"
        stderr_path = self.run_dir / f"{stamp}.stderr.log"
        final_path = self.run_dir / f"{stamp}.final.txt"
        resume_token = str(request.arguments.get("codex_resume_token") or "").strip()
        permission_hash = str(request.arguments.get("codex_permission_hash") or "").strip()
        command = [
            codex,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--cd",
            str(self.workspace),
            "--sandbox",
            "workspace-write",
            "--output-last-message",
            str(final_path),
            goal,
        ]
        started = time.time()
        env = os.environ.copy()
        if resume_token:
            env["AGENT_COMPANION_CODEX_RESUME_TOKEN"] = resume_token
        if permission_hash:
            env["AGENT_COMPANION_CODEX_PERMISSION_HASH"] = permission_hash
        try:
            with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
                result = subprocess.run(command, cwd=str(self.workspace), stdout=stdout, stderr=stderr, text=True, env=env)
        except OSError:
            return self._failed(
                "本地 Codex 没有启动成功。请确认 Codex CLI 已安装，并且配置指向可执行程序。",
                {"error": "codex_launch_failed"},
                status="not_available",
                voice_text="我没能启动本地 Codex，先停在这里。",
            )
        elapsed = time.time() - started
        artifacts = [self._rel(stdout_path), self._rel(stderr_path), self._rel(final_path)]
        codex_run, permission = build_codex_run_state(
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            final_path=final_path,
            returncode=result.returncode,
            elapsed_seconds=elapsed,
            artifact_labels=[
                {"kind": "event_log", "label": "Codex 事件记录"},
                {"kind": "error_log", "label": "Codex 错误输出"},
                {"kind": "final_summary", "label": "Codex 最终摘要"},
            ],
        )
        if permission is not None and permission.get("resumable"):
            approval_arguments = codex_permission_approval_arguments(goal, permission)
            return ToolResult(
                ok=False,
                agent_state={
                    "tool": self.name,
                    "codex_run": codex_run,
                    "approval_request": {
                        "tool": self.name,
                        "arguments": approval_arguments,
                        "reason": "Codex 请求一个外部权限确认，Joi 会在一次性确认后继续。",
                    },
                },
                display_card=DisplayCard(
                    "Codex 等待权限",
                    "Codex 需要权限确认。",
                    codex_card_body(codex_run),
                    status="approval",
                    artifacts=artifacts,
                ),
                voice_line=safe_voice_line("这一步需要你确认后我再继续。", sprite="4"),
                requires_approval=True,
                risk=RiskLevel.MEDIUM,
            )

        if permission is not None:
            codex_run["safe_summary"] = PERMISSION_FAIL_CLOSED_MESSAGE

        ok = result.returncode == 0 and codex_run.get("status") == "completed"
        summary = codex_run.get("safe_summary") or ("Codex 已完成。" if ok else f"Codex 失败，退出码 {result.returncode}。")
        return ToolResult(
            ok=ok,
            agent_state={
                "tool": self.name,
                "returncode": result.returncode,
                "elapsed_seconds": elapsed,
                "codex_run": codex_run,
            },
            display_card=DisplayCard(
                "Codex 任务",
                str(summary),
                codex_card_body(codex_run),
                status="success" if ok else "failed",
                artifacts=artifacts,
            ),
            voice_line=safe_voice_line("写码任务完成了。" if ok else "写码任务没有跑通，可以展开执行过程查看细节。", sprite="5" if ok else "4"),
        )

    def _failed(self, message: str, state: dict, status: str, voice_text: str = "写码能力还没有准备好。") -> ToolResult:
        return ToolResult(
            ok=False,
            agent_state={
                "tool": self.name,
                **state,
                "codex_run": {
                    "status": status,
                    "elapsed_seconds": 0,
                    "returncode": None,
                    "safe_summary": message,
                    "permission_required": False,
                    "artifacts": [],
                    "events": [],
                },
            },
            display_card=DisplayCard("Codex 任务", message, status="failed"),
            voice_line=safe_voice_line(voice_text, sprite="4"),
        )

    @staticmethod
    def _codex_executable() -> str:
        return codex_executable()

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return str(path)
