from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from agent_companion.core.agent_cli import AgentCliProfile, agent_cli_profile, resolve_agent_cli_executable
from agent_companion.core.codex_events import codex_card_body, sanitized_codex_text
from agent_companion.core.schemas import DisplayCard, RiskLevel, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.tools.codex import CodexTool
from agent_companion.core.voice import safe_voice_line


class AgentCliRunTool(ToolAdapter):
    name = "agent_cli.run"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.run_dir = workspace / "data" / "agent_companion" / "agent_cli_runs"

    def run(self, request: ToolRequest) -> ToolResult:
        goal = " ".join(str(request.arguments.get("goal") or request.arguments.get("text") or "").split()).strip()
        if not goal:
            return self._failed("Agent CLI 接管目标为空。", {"error": "empty_goal"})
        cli_id = _safe_cli_id(request.arguments.get("cli_id") or "codex")
        profile = agent_cli_profile(cli_id)
        if profile is None:
            return self._failed("未知 Agent CLI。", {"error": "unknown_cli", "cli_id": cli_id})
        if not profile.supports_takeover:
            return self._failed(
                f"{profile.name} 已发现，但还没有接入 Joi 接管适配器。",
                {"error": "takeover_not_supported", "cli_id": profile.id},
            )
        executable = resolve_agent_cli_executable(profile.id)
        if not executable:
            return self._failed(f"没有找到本地 {profile.name}。", {"error": "cli_not_found", "cli_id": profile.id})

        prompt = _takeover_prompt(
            goal,
            profile=profile,
            model=str(request.arguments.get("model") or "默认"),
            reasoning=str(request.arguments.get("reasoning") or "默认"),
            memory_context=request.arguments.get("memory_context"),
            desktop_context=request.arguments.get("desktop_context"),
            background_context=request.arguments.get("background_context"),
        )
        if profile.run_strategy == "codex_exec_json":
            return self._run_codex(request, profile, prompt, goal)
        if profile.run_strategy == "prompt_arg":
            return self._run_prompt_cli(request, profile, executable, prompt)
        return self._failed(
            f"{profile.name} 已发现，但还没有接入 Joi 接管适配器。",
            {"error": "takeover_not_supported", "cli_id": profile.id},
        )

    def _run_codex(self, request: ToolRequest, profile: AgentCliProfile, prompt: str, original_goal: str) -> ToolResult:
        arguments = {"goal": prompt}
        for key in ("codex_resume_token", "codex_permission_hash", "codex_permission_decision"):
            value = str(request.arguments.get(key) or "").strip()
            if value:
                arguments[key] = value
        result = CodexTool(self.workspace).run(ToolRequest("codex.run", arguments, request.reason))
        state = dict(result.agent_state)
        codex_run = state.get("codex_run") if isinstance(state.get("codex_run"), dict) else {}
        state["tool"] = self.name
        state["source_tool"] = "codex.run"
        state["agent_cli"] = _agent_cli_state(
            profile,
            model=str(request.arguments.get("model") or "默认"),
            reasoning=str(request.arguments.get("reasoning") or "默认"),
        )
        state["agent_cli_takeover"] = True
        state["agent_cli_run"] = {
            "status": str(codex_run.get("status") or ("completed" if result.ok else "failed")),
            "source": "codex.run",
            "safe_summary": str(codex_run.get("safe_summary") or result.display_card.summary),
        }
        approval_request = state.get("approval_request") if isinstance(state.get("approval_request"), dict) else None
        if result.requires_approval and approval_request:
            approval_args = approval_request.get("arguments") if isinstance(approval_request.get("arguments"), dict) else {}
            state["approval_request"] = {
                "tool": self.name,
                "arguments": {
                    "goal": original_goal,
                    "cli_id": profile.id,
                    "model": str(request.arguments.get("model") or "默认"),
                    "reasoning": str(request.arguments.get("reasoning") or "默认"),
                    "codex_resume_token": str(approval_args.get("codex_resume_token") or ""),
                    "codex_permission_hash": str(approval_args.get("codex_permission_hash") or ""),
                    "codex_permission_decision": str(approval_args.get("codex_permission_decision") or "approved"),
                },
                "reason": "Agent CLI 请求一个外部权限确认，Joi 会在一次性确认后继续。",
            }
        title = "Agent CLI 等待权限" if result.requires_approval else "Agent CLI 接管"
        status = "approval" if result.requires_approval else ("success" if result.ok else "failed")
        summary = "Agent CLI 需要权限确认。" if result.requires_approval else _agent_cli_summary(result.ok, profile, result.display_card.summary)
        body = codex_card_body(codex_run) if codex_run else result.display_card.body
        return ToolResult(
            ok=result.ok,
            agent_state=state,
            display_card=DisplayCard(title, summary, body, status=status, artifacts=result.display_card.artifacts),
            voice_line=safe_voice_line(
                "Agent CLI 接管完成了。" if result.ok else ("这一步需要你确认后我再继续。" if result.requires_approval else "Agent CLI 没有跑通，细节在卡片里。"),
                sprite="5" if result.ok else "4",
            ),
            requires_approval=result.requires_approval,
            risk=result.risk,
        )

    def _run_prompt_cli(self, request: ToolRequest, profile: AgentCliProfile, executable: str, prompt: str) -> ToolResult:
        run_dir = self.run_dir / profile.id
        run_dir.mkdir(parents=True, exist_ok=True)
        stamp = str(int(time.time() * 1000))
        stdout_path = run_dir / f"{stamp}.stdout.log"
        stderr_path = run_dir / f"{stamp}.stderr.log"
        final_path = run_dir / f"{stamp}.final.txt"
        command = [executable, *profile.prompt_args, prompt]
        started = time.time()
        returncode: int | None = None
        timed_out = False
        try:
            with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
                completed = subprocess.run(
                    command,
                    cwd=str(self.workspace),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                    timeout=_agent_cli_timeout_seconds(),
                    env=os.environ.copy(),
                )
                returncode = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
        except OSError:
            return self._failed(f"{profile.name} 没有启动成功。", {"error": "cli_launch_failed", "cli_id": profile.id})
        elapsed = time.time() - started
        output = _tail_text(stdout_path)
        if output:
            final_path.write_text(output, encoding="utf-8")
        artifacts = [self._rel(stdout_path), self._rel(stderr_path), self._rel(final_path)]
        safe_output = sanitized_codex_text(output, f"{profile.name} 已返回结果。")
        ok = bool(returncode == 0 and not timed_out)
        status = "timeout" if timed_out else ("completed" if ok else "failed")
        summary = f"{profile.name} 已完成这轮 Joi 请求。" if ok else (f"{profile.name} 执行超时。" if timed_out else f"{profile.name} 没有跑通，退出码 {returncode}。")
        state = {
            "tool": self.name,
            "agent_cli_takeover": True,
            "agent_cli": _agent_cli_state(
                profile,
                model=str(request.arguments.get("model") or "默认"),
                reasoning=str(request.arguments.get("reasoning") or "默认"),
            ),
            "agent_cli_run": {
                "status": status,
                "elapsed_seconds": round(max(0.0, elapsed), 2),
                "returncode": returncode,
                "safe_summary": summary,
                "artifacts": [
                    {"kind": "output_log", "label": "Agent CLI 输出"},
                    {"kind": "error_log", "label": "Agent CLI 错误输出"},
                    {"kind": "final_summary", "label": "Agent CLI 最终摘要"},
                ],
                "events": [{"category": "final" if ok else "error", "summary": safe_output}],
            },
        }
        return ToolResult(
            ok=ok,
            agent_state=state,
            display_card=DisplayCard(
                "Agent CLI 接管",
                summary,
                _agent_cli_card_body(state["agent_cli_run"]),
                status="success" if ok else "failed",
                artifacts=artifacts,
            ),
            voice_line=safe_voice_line("Agent CLI 接管完成了。" if ok else "Agent CLI 没有跑通，细节在卡片里。", sprite="5" if ok else "4"),
            risk=RiskLevel.MEDIUM,
        )

    def _failed(self, message: str, state: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=False,
            agent_state={"tool": self.name, "agent_cli_takeover": True, **state},
            display_card=DisplayCard("Agent CLI 接管", message, status="failed"),
            voice_line=safe_voice_line("Agent CLI 还没有准备好。", sprite="4"),
            risk=RiskLevel.MEDIUM,
        )

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return str(path)


def _takeover_prompt(
    goal: str,
    *,
    profile: AgentCliProfile,
    model: str,
    reasoning: str,
    memory_context: object,
    desktop_context: object,
    background_context: object,
) -> str:
    sections = [
        "你现在作为 Joi 的本机 Agent CLI 主执行器接管这一轮请求。",
        "Joi 继续负责桌面 UI、审批、语音、记忆和结果展示；你负责理解用户目标并推进，不要把自己限制为写代码任务。",
        "请用中文返回简洁结果。若当前 CLI 不能直接完成真实桌面、浏览器或系统操作，请明确说明需要 Joi 侧 Computer Use 或用户审批接续，不要编造已完成。",
    ]
    preferences = []
    if model and model != "默认":
        preferences.append(f"模型偏好：{model}（若 CLI 无法切换则使用自身默认配置）")
    if reasoning and reasoning != "默认":
        preferences.append(f"推理强度偏好：{reasoning}")
    if preferences:
        sections.append("执行偏好：" + "；".join(preferences))
    context = _context_block(memory_context, desktop_context, background_context)
    if context:
        sections.append("Joi 侧上下文（安全摘要）：\n" + context)
    sections.append(f"接管 CLI：{profile.name}")
    sections.append("用户请求：\n" + goal)
    return "\n\n".join(sections)


def _context_block(memory_context: object, desktop_context: object, background_context: object) -> str:
    lines: list[str] = []
    if isinstance(desktop_context, dict):
        site = str(desktop_context.get("site") or "").strip()
        browser = str(desktop_context.get("browser") or "").strip()
        if site:
            lines.append(f"- 当前桌面站点：{site}（浏览器：{browser or 'unknown'}）")
    if isinstance(background_context, dict):
        active = background_context.get("active_scope") if isinstance(background_context.get("active_scope"), dict) else {}
        label = str(active.get("label") or "").strip()
        if label:
            lines.append(f"- 后台上下文范围：{label}")
        recent = background_context.get("recent_context") if isinstance(background_context.get("recent_context"), list) else []
        for row in recent[-3:]:
            if isinstance(row, dict):
                summary = " ".join(str(row.get("summary") or "").split()).strip()
                if summary:
                    lines.append(f"- 背景摘要：{summary[:220]}")
    if isinstance(memory_context, list):
        for row in memory_context[:5]:
            if isinstance(row, dict):
                text = " ".join(str(row.get("text") or "").split()).strip()
                if text:
                    lines.append(f"- 记忆：{text[:220]}")
    return "\n".join(lines[:10])


def _agent_cli_state(profile: AgentCliProfile, *, model: str, reasoning: str) -> dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        "vendor": profile.vendor,
        "model": model or "默认",
        "reasoning": reasoning or "默认",
        "run_strategy": profile.run_strategy,
    }


def _agent_cli_summary(ok: bool, profile: AgentCliProfile, fallback: str) -> str:
    if ok:
        return f"{profile.name} 已完成这轮 Joi 请求。"
    return sanitized_codex_text(fallback, f"{profile.name} 没有跑通。")


def _agent_cli_card_body(run_state: dict[str, Any]) -> str:
    lines = [str(run_state.get("safe_summary") or "Agent CLI 状态已更新。")]
    returncode = run_state.get("returncode")
    if returncode is not None:
        lines.append(f"退出码：{returncode}")
    events = run_state.get("events") if isinstance(run_state.get("events"), list) else []
    for event in events[-5:]:
        if isinstance(event, dict) and event.get("summary"):
            lines.append(f"- {event['summary']}")
    artifacts = run_state.get("artifacts") if isinstance(run_state.get("artifacts"), list) else []
    labels = [str(item.get("label") or "") for item in artifacts if isinstance(item, dict) and item.get("label")]
    if labels:
        lines.append("产物：" + "、".join(labels[:4]))
    return "\n".join(lines)


def _tail_text(path: Path, *, max_chars: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max_chars:]


def _agent_cli_timeout_seconds() -> int:
    try:
        return max(30, min(3600, int(os.environ.get("AGENT_COMPANION_AGENT_CLI_TIMEOUT_SECONDS", "900"))))
    except ValueError:
        return 900


def _safe_cli_id(value: object) -> str:
    text = str(value or "").strip().casefold()
    return "".join(char for char in text if char.isalnum() or char in "_-")[:64] or "codex"
