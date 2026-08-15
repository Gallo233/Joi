from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent_companion.core.codex_events import sanitized_codex_text
from agent_companion.core.codex_support import codex_executable, permission_fingerprint
from agent_companion.core.config import load_workspace_config
from agent_companion.core.language_policy import (
    chat_language_policy,
    language_mismatch_fallback,
    obvious_language_mismatch,
    reply_language_instruction,
)
from agent_companion.core.model_call import CallBudget
from agent_companion.core.provider_client import chat_completion
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType
from agent_companion.core.voice import safe_voice_line


RuntimeEmitter = Callable[[AgentEvent], None]
_MODEL_ARG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_REASONING_ARGS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}


@dataclass
class RuntimeApproval:
    approval_id: str
    task_id: str
    resume_token: str = ""
    permission_hash: str = ""
    tool: str = "external_permission"


class CodexRuntimeSession:
    """Joi-facing Codex runtime.

    This keeps Joi as the shell and Codex as the primary engine. The session uses
    `codex exec --json` for the first turn and `codex exec resume` for following
    turns so the UI can consume structured events without pretending Codex is only
    a coding task.
    """

    def __init__(self, workspace: Path, app: Any, emit: RuntimeEmitter) -> None:
        self.workspace = workspace.resolve()
        self.app = app
        self.emit = emit
        self.run_dir = self.workspace / "data" / "agent_companion" / "codex_runtime"
        self.state_path = self.workspace / "data" / "agent_companion" / "runtime_state.json"
        self._lock = threading.RLock()
        self._running = False
        self._pending: dict[str, RuntimeApproval] = {}
        self.state: dict[str, Any] = self._load_state()

    def configure(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        with self._lock:
            if "enabled" in params:
                self.state["enabled"] = bool(params.get("enabled"))
            if "selected" in params or "cli_id" in params:
                self.state["selected"] = _safe_token(params.get("selected") or params.get("cli_id") or "codex")
            if "mode" in params:
                self.state["mode"] = _safe_mode(params.get("mode"))
            if "model" in params:
                self.state["model"] = _safe_label(params.get("model") or "默认")
            if "reasoning" in params:
                self.state["reasoning"] = _safe_label(params.get("reasoning") or "默认")
            if self.state.get("mode") != "local_cli":
                self.state["enabled"] = False
            self._save_state()
            return self.status_payload()

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.state.get("selected") == "codex" and self._codex_executable():
                self.state["enabled"] = True
                self.state["mode"] = "local_cli"
                self._save_state()
            return self.status_payload()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self.state["enabled"] = False
            self._save_state()
            return self.status_payload()

    def should_handle(self, text: str) -> bool:
        if not str(text or "").strip():
            return False
        if not bool(self.state.get("enabled", True)):
            return False
        if str(self.state.get("mode") or "local_cli") != "local_cli":
            return False
        return str(self.state.get("selected") or "codex") == "codex"

    def status_payload(self) -> dict[str, Any]:
        codex = self._codex_executable()
        pending = bool(self._pending)
        return {
            "safe_for_display": True,
            "enabled": bool(self.state.get("enabled", True)),
            "mode": _safe_mode(self.state.get("mode") or "local_cli"),
            "selected": _safe_token(self.state.get("selected") or "codex"),
            "model": _safe_label(self.state.get("model") or "默认"),
            "reasoning": _safe_label(self.state.get("reasoning") or "XHigh"),
            "available": bool(codex),
            "status": "waiting_approval" if pending else ("running" if self._running else ("ready" if codex else "unavailable")),
            "session": "active" if self.state.get("session_id") or self.state.get("has_session") else "new",
            "mcp_connected": bool(self.state.get("mcp_connected")),
            "pending_approvals": len(self._pending),
        }

    def mark_mcp_connected(self, connected: bool) -> None:
        with self._lock:
            self.state["mcp_connected"] = bool(connected)
            self._save_state()

    def run_user_text(self, text: str) -> dict[str, Any]:
        user_text = " ".join(str(text or "").strip().split())
        task_id = f"runtime-{uuid.uuid4().hex[:10]}"
        if not user_text:
            return {"ok": False, "error": "empty_text"}
        self.emit(
            AgentEvent(
                EventType.USER_MESSAGE,
                task_id,
                DisplayCard("你", user_text),
                safe_voice_line("", fallback=""),
                {"intent": "codex_runtime", "runtime_event": "user_message"},
            )
        )
        return self._run_prompt(task_id, user_text, include_harness=True)

    def resolve_approval(self, approval_id: str, approved: bool) -> dict[str, Any] | None:
        with self._lock:
            approval = self._pending.pop(approval_id, None)
        if approval is None:
            return None
        decision_text = (
            "用户已经在 Joi 里批准了刚才的权限请求。请继续完成这轮请求，并简洁汇报结果。"
            if approved
            else "用户拒绝了刚才的权限请求。请停止该动作，并说明没有继续执行。"
        )
        return self._run_prompt(approval.task_id, decision_text, include_harness=False, approval=approval, approved=approved)

    def has_pending_approval(self, approval_id: str) -> bool:
        with self._lock:
            return approval_id in self._pending

    def pending_approval_ids(self) -> list[str]:
        with self._lock:
            return list(self._pending)

    def _run_prompt(
        self,
        task_id: str,
        user_text: str,
        *,
        include_harness: bool,
        approval: RuntimeApproval | None = None,
        approved: bool | None = None,
    ) -> dict[str, Any]:
        codex = self._codex_executable()
        if not codex:
            self.emit(
                AgentEvent(
                    EventType.RUNTIME_ERROR,
                    task_id,
                    DisplayCard("Joi", "本机运行环境还没有准备好。", status="failed"),
                    safe_voice_line("本机运行环境还没有准备好。", sprite="4"),
                    {"runtime_event": "runtime_error", "error": "codex_not_found"},
                )
            )
            return {"ok": False, "error": "codex_not_found"}
        with self._lock:
            if self._running:
                self.emit(
                    AgentEvent(
                        EventType.RUNTIME_ERROR,
                        task_id,
                        DisplayCard("Joi", "上一轮还在处理，我先排队等它结束。", status="failed"),
                        safe_voice_line("上一轮还在处理，我先等它结束。", sprite="4"),
                        {"runtime_event": "runtime_busy"},
                    )
                )
                return {"ok": False, "error": "runtime_busy"}
            self._running = True
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            stamp = str(int(time.time() * 1000))
            stdout_path = self.run_dir / f"{stamp}.stdout.jsonl"
            stderr_path = self.run_dir / f"{stamp}.stderr.log"
            final_path = self.run_dir / f"{stamp}.final.txt"
            prompt = self._build_prompt(user_text, include_harness=include_harness)
            command = self._command(codex, prompt, final_path, resume=bool(self.state.get("has_session") or self.state.get("session_id")))
            self.emit(
                AgentEvent(
                    EventType.RUNTIME_STARTED,
                    task_id,
                    DisplayCard("Joi", "我在处理。"),
                    safe_voice_line("我在处理。", sprite="3"),
                    {
                        "runtime_event": "runtime_started",
                        "runtime": "joi",
                        "artifacts": [self._rel(stdout_path), self._rel(stderr_path), self._rel(final_path)],
                    },
                )
            )
            env = os.environ.copy()
            if approval is not None:
                env["AGENT_COMPANION_CODEX_PERMISSION_DECISION"] = "approved" if approved else "denied"
                if approval.resume_token:
                    env["AGENT_COMPANION_CODEX_RESUME_TOKEN"] = approval.resume_token
                if approval.permission_hash:
                    env["AGENT_COMPANION_CODEX_PERMISSION_HASH"] = approval.permission_hash
            returncode = self._run_process(command, env, stdout_path, stderr_path, task_id)
            if self.has_pending_for_task(task_id):
                return {"ok": False, "submitted": True, "pending_approval": True}
            final_text = _safe_joi_text(_read_text(final_path), "我处理完了。")
            if returncode == 0:
                final_text, language_repaired, display_language = self._ensure_display_language(user_text, final_text)
                self.emit(
                    AgentEvent(
                        EventType.RUNTIME_FINAL,
                        task_id,
                        DisplayCard("Joi", final_text, status="success"),
                        safe_voice_line(_voice_summary(final_text), sprite="5"),
                        {
                            "runtime_event": "runtime_final",
                            "ok": True,
                            "display_language": display_language,
                            "display_language_repaired": language_repaired,
                        },
                    )
                )
                self._record_memory_candidate(task_id, user_text, final_text)
                return {"ok": True, "submitted": True}
            detail = _safe_joi_text(_read_text(stderr_path), "这轮没有跑通。")
            self.emit(
                AgentEvent(
                    EventType.RUNTIME_ERROR,
                    task_id,
                    DisplayCard("Joi", "这轮没有跑通。", detail, status="failed"),
                    safe_voice_line("这轮没有跑通。", sprite="4"),
                    {"runtime_event": "runtime_error", "returncode": returncode},
                )
            )
            return {"ok": False, "error": "runtime_failed", "returncode": returncode}
        finally:
            with self._lock:
                self._running = False
                self._save_state()

    def has_pending_for_task(self, task_id: str) -> bool:
        return any(row.task_id == task_id for row in self._pending.values())

    def _chat_language(self) -> str:
        """The language the user set for what Joi writes, not for what she says."""

        reader = getattr(getattr(self, "app", None), "chat_language", None)
        try:
            return str(reader() or "") if callable(reader) else ""
        except Exception:
            return ""

    def _ensure_display_language(self, user_text: str, final_text: str) -> tuple[str, bool, str]:
        """Fail closed when Codex lets the localized persona choose display text."""

        chat_language = self._chat_language()
        policy = chat_language_policy(chat_language, user_text)
        if not obvious_language_mismatch(user_text, final_text, chat_language):
            return final_text, False, policy.code
        config = load_workspace_config(self.workspace)
        if config is not None and config.llm.is_configured:
            wanted = f"用户选定的聊天语言（{policy.label}）" if policy.chosen else f"用户本轮输入所用的同一种自然语言（提示：{policy.label}）"
            system_prompt = (
                "你只修复最终屏幕回复的语言，不回答新问题。"
                f"把 reply 改写为{wanted}，"
                "保持原意、事实边界和角色语气，不添加完成状态或新信息。"
                "角色的配音语言与这里无关。只输出 JSON：{\"reply\":\"修复后的屏幕回复\"}。"
            )
            repair_input = json.dumps(
                {"user_message": user_text, "reply": final_text},
                ensure_ascii=False,
            )
            try:
                outcome = chat_completion(
                    config.llm,
                    "fast",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": repair_input},
                    ],
                    budget=CallBudget(timeout_ms=8_000, max_fallbacks=0, allow_categories=("text",)),
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    instructions=system_prompt,
                    user_input=repair_input,
                )
                payload = json.loads(str(outcome.value or "{}")) if outcome.ok else {}
                repaired = str(payload.get("reply") or "").strip() if isinstance(payload, dict) else ""
                if repaired and not obvious_language_mismatch(user_text, repaired, chat_language):
                    return _safe_joi_text(repaired, final_text), True, policy.code
            except Exception:
                pass
        fallback = language_mismatch_fallback(policy)
        return (fallback or final_text), bool(fallback), policy.code

    def _run_process(
        self,
        command: list[str],
        env: dict[str, str],
        stdout_path: Path,
        stderr_path: Path,
        task_id: str,
    ) -> int:
        with stdout_path.open("w", encoding="utf-8") as stdout_log, stderr_path.open("w", encoding="utf-8") as stderr_log:
            proc = subprocess.Popen(
                command,
                cwd=str(self.workspace),
                stdout=subprocess.PIPE,
                stderr=stderr_log,
                text=True,
                bufsize=1,
                env=env,
            )
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                stdout_log.write(raw_line)
                stdout_log.flush()
                self._handle_json_line(raw_line, task_id)
                if self.has_pending_for_task(task_id):
                    proc.terminate()
                    try:
                        return proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        return proc.wait()
            return proc.wait()

    def _handle_json_line(self, raw_line: str, task_id: str) -> None:
        line = raw_line.strip()
        if not line:
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        session_id = _find_first_string(payload, ("session_id", "conversation_id", "thread_id"))
        if session_id:
            self.state["session_id"] = session_id[:128]
            self.state["has_session"] = True
        permission = _permission_from_payload(payload)
        if permission is not None:
            approval_id = f"runtime-approval-{uuid.uuid4().hex[:12]}"
            approval = RuntimeApproval(
                approval_id=approval_id,
                task_id=task_id,
                resume_token=str(permission.get("resume_token") or ""),
                permission_hash=str(permission.get("permission_hash") or ""),
                tool=str(permission.get("tool") or "external_permission"),
            )
            with self._lock:
                self._pending[approval_id] = approval
            self.emit(
                AgentEvent(
                    EventType.APPROVAL_REQUIRED,
                    task_id,
                    DisplayCard("需要确认", "Joi 需要你确认后继续。", _safe_joi_text(permission.get("summary"), "权限请求已准备好。"), status="approval"),
                    safe_voice_line("这一步需要你确认后我再继续。", sprite="4"),
                    {
                        "runtime_event": "approval_requested",
                        "approval": {
                            "approval_id": approval_id,
                            "task_id": task_id,
                            "tool": "runtime.codex_permission",
                        },
                        "permission": {
                            "tool": approval.tool,
                            "resumable": bool(permission.get("resumable", True)),
                        },
                    },
                )
            )
            return
        summary = _event_summary(payload)
        if summary:
            self.emit(
                AgentEvent(
                    EventType.RUNTIME_DELTA,
                    task_id,
                    DisplayCard("Joi", summary),
                    safe_voice_line("", fallback=""),
                    {"runtime_event": "runtime_delta"},
                )
            )

    def _command(self, codex: str, prompt: str, final_path: Path, *, resume: bool) -> list[str]:
        runtime_overrides = _codex_runtime_overrides(self.state.get("model"), self.state.get("reasoning"))
        if resume:
            command = [codex, "exec", "resume", "--json", "--output-last-message", str(final_path), *runtime_overrides]
            session_id = str(self.state.get("session_id") or "").strip()
            if session_id:
                command.append(session_id)
            else:
                command.append("--last")
            command.append(prompt)
            return command
        return [
            codex,
            "exec",
            *runtime_overrides,
            "--json",
            "--skip-git-repo-check",
            "--cd",
            str(self.workspace),
            "--sandbox",
            "workspace-write",
            "--output-last-message",
            str(final_path),
            prompt,
        ]

    def _build_prompt(self, user_text: str, *, include_harness: bool) -> str:
        if not include_harness:
            return user_text
        sections = [
            "你是 Joi 的主执行内核，但用户只应感知到 Joi 这个角色。",
            reply_language_instruction(user_text, self._chat_language()),
            "不要把自己描述成 Codex，也不要把普通请求说成写代码任务。",
            "Joi 桌面壳负责显示、语音、设置、记忆和陪看状态；你负责理解用户目标并持续推进。",
            "最终回复先给结果，默认控制在 2 至 5 个短句；只有确实需要时才列出不超过 4 项。不要重复用户请求，也不要逐条复述内部执行日志。",
            "如果需要操作本机界面，优先通过本地 joi MCP 工具完成：先用 joi_screen_observe 看当前画面，再用 joi_computer_* 或 joi_browser_* 执行动作，动作后继续观察验证。",
            "如果用户要点击当前画面里的按钮、链接、标签、菜单或某个自然语言目标，优先调用 joi_computer_click_target(query='目标描述', goal='用户完整目标')，让 Joi 负责观察、定位、审批和点击；只有明确坐标时才直接用 joi_computer_click。",
            "joi_computer_click / double_click / drag 的坐标默认使用最近一次 joi_screen_observe 返回截图里的像素坐标；Joi 会转换成真实屏幕坐标。",
            "浏览器任务优先用 joi_browser_open_url 或 joi_browser_open_site，而不是 shell 的 open 命令；例如打开 B 站热门视频：joi_browser_open_site(site='b站', section='热门')，随后 joi_goal_verify(goal='帮我打开B站热门视频')。",
            "如果用户说“再”“继续”“当前页面”“这个页面上”或要求点击现有网页元素，不要新开浏览器；先用 joi_browser_current_state / joi_screen_observe 复用当前上下文，再用 joi_computer_click_target。",
            "网页加载、跳转或动画后可以调用只读的 joi_computer_wait(seconds=2, observe_after=true)，再继续 joi_goal_verify。",
            "joi_computer_* / joi_browser_* / joi_computer_click_target 返回里如果有 continuation_context，优先按其中的 next_tool 和 suggested_arguments 继续；这比从自然语言摘要里猜下一步更可靠。",
            "需要确认浏览器是否到达正确页面时，可以先用只读的 joi_browser_current_state 获取 Safari/Chrome/Edge 当前标签标题和 URL。",
            "如果用户说“刚才/再/当前/打开了吗”等上下文问题，先用 joi_screen_observe 或 joi_goal_verify 检查当前画面，不要把短语拆成新的搜索词。",
            "不要只因为命令执行或画面有变化就说完成；只有观察到用户目标确实达成后才向用户确认完成。",
            "完成前优先调用 joi_goal_verify 校验当前画面是否满足用户目标；如果 status 不是 met，继续观察或执行下一步。",
            "Joi 会把 joi_computer_* / joi_browser_open_* 的权限请求显示给用户，用户批准后工具才会返回执行结果；不要再做二次审批。",
            "陪看、记忆、skills 和语音状态通过 joi_watch_*、joi_memory_*、joi_skills_*、joi_voice_status 使用。",
        ]
        character = getattr(self.app, "character", None)
        if character is not None:
            sections.append("Joi 角色设定：\n" + character.prompt_header()[:2400])
        memory_context = self.app.memory.context(8, query=user_text, **self.app._memory_scope())
        if memory_context:
            lines = [str(row.get("text") or "") for row in memory_context[:8] if isinstance(row, dict) and row.get("text")]
            if lines:
                sections.append("可用记忆摘要：\n" + "\n".join(f"- {line[:240]}" for line in lines))
        background = self.app.background_context.status()
        if background.get("active") or background.get("recent_context"):
            sections.append("Joi 当前状态：\n" + _compact_json(background, 1600))
        sections.append("用户请求：\n" + user_text)
        # Repeat after the localized persona and memory: those may legitimately
        # be Japanese while a Chinese user message still requires Chinese
        # display text.  The selected voice language is handled later by the
        # expression/TTS channel and never changes this final answer contract.
        sections.append("最终显示回复语言（最高优先级）：\n" + reply_language_instruction(user_text, self._chat_language()))
        return "\n\n".join(sections)

    def _record_memory_candidate(self, task_id: str, user_text: str, final_text: str) -> None:
        # Runtime outcomes are execution history, not durable facts about the user.
        # Persisting every result polluted long-term memory with task receipts.
        return

    def _load_state(self) -> dict[str, Any]:
        default = {
            "enabled": True,
            "mode": "local_cli",
            "selected": "codex",
            "model": "默认",
            "reasoning": "XHigh",
            "mcp_connected": False,
            "has_session": False,
        }
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return default
        if not isinstance(raw, dict):
            return default
        merged = {**default, **raw}
        merged["selected"] = _safe_token(merged.get("selected") or "codex")
        merged["mode"] = _safe_mode(merged.get("mode") or "local_cli")
        return merged

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.state_path)

    @staticmethod
    def _codex_executable() -> str:
        return codex_executable()

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return str(path)


def _codex_runtime_overrides(model: object, reasoning: object) -> list[str]:
    overrides: list[str] = []
    model_id = str(model or "").strip()
    if model_id and model_id != "默认" and _MODEL_ARG_RE.fullmatch(model_id):
        overrides.extend(("--model", model_id))
    effort = re.sub(r"[\s_-]+", "", str(reasoning or "").strip().casefold())
    if effort in _REASONING_ARGS:
        overrides.extend(("--config", f'model_reasoning_effort="{effort}"'))
    return overrides


def _permission_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    markers = " ".join(str(payload.get(key) or "") for key in ("type", "event", "kind", "status", "message")).casefold()
    if not any(token in markers for token in ("permission", "approval", "escalation")):
        return None
    return {
        "summary": _event_summary(payload) or "权限请求已准备好。",
        "tool": _find_first_string(payload, ("tool", "tool_name", "action", "command")) or "external_permission",
        "resume_token": _find_first_string(payload, ("resume_token", "continuation_token", "resume_id", "request_id", "id")),
        "permission_hash": permission_fingerprint(payload),
        "resumable": True,
    }


def _event_summary(payload: dict[str, Any]) -> str:
    for key in ("message", "summary", "text", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            text = _safe_joi_text(value, "")
            if text:
                return text
    nested = payload.get("item")
    if isinstance(nested, dict):
        return _event_summary(nested)
    return ""


def _find_first_string(value: object, keys: tuple[str, ...]) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, str) and item.strip():
                return item.strip()
        for item in value.values():
            found = _find_first_string(item, keys)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_first_string(item, keys)
            if found:
                return found
    return ""


def _safe_joi_text(value: object, fallback: str = "Joi 状态已更新。") -> str:
    text = sanitized_codex_text(value, fallback)
    text = re.sub(r"\bCodex\b", "Joi", text, flags=re.IGNORECASE)
    return text[:1200] if text else fallback


def _voice_summary(text: str) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return "我处理完了。"
    parts = re.split(r"(?<=[。！？!?])\s*", cleaned)
    return (parts[0] if parts and parts[0] else cleaned)[:120]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _compact_json(value: object, limit: int) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)[:limit]


def _safe_token(value: object) -> str:
    text = str(value or "").strip().casefold().replace(" ", "_")
    return "".join(char for char in text if char.isalnum() or char in "_-")[:64] or "codex"


def _safe_mode(value: object) -> str:
    text = _safe_token(value)
    return text if text in {"local_cli", "byok"} else "local_cli"


def _safe_label(value: object) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:80] or "默认"
