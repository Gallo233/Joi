from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import time
import uuid

from agent_companion.core.computer_use import (
    COMPUTER_AUDIT_STATE_KEY,
    ComputerUseAuditEvent,
    audit_state,
    computer_action_audit_event,
    computer_approval_audit_event,
    target_grounding_audit_events,
)
from agent_companion.core.character import CharacterHarness, load_character
from agent_companion.core.config import AppConfig, ModelRouter, load_app_config
from agent_companion.core.codex_events import codex_cancel_run_state
from agent_companion.core.event_bus import EventBus
from agent_companion.core.expression import ExpressionEngine
from agent_companion.core.memory import MemoryStore
from agent_companion.core.planner import build_plan
from agent_companion.core.policy import PolicyGate
from agent_companion.core.schemas import AgentEvent, AgentPlan, DisplayCard, EventType, RiskLevel, ToolRequest, ToolResult
from agent_companion.core.tools.browser import BrowserTool
from agent_companion.core.tools.chat import CompanionChatTool
from agent_companion.core.tools.codex import CodexTool
from agent_companion.core.tools.computer import ComputerActionTool
from agent_companion.core.tools.files import FileReadTool
from agent_companion.core.tools.game_ok_ww import OkWwTool
from agent_companion.core.tools.mcp import McpListTool
from agent_companion.core.tools.registry import ToolRegistry
from agent_companion.core.tools.runtime_config import RuntimeConfigUpdateTool
from agent_companion.core.tools.screen_observe import ScreenObserveTool
from agent_companion.core.tools.targeting import PendingSemanticTargetSelection, SemanticTargetSelectionStore, SemanticTargetSelectionTool, SemanticTargetTool
from agent_companion.core.tools.watch import WatchRecallTool
from agent_companion.core.vision.ocr import PytesseractOcrExtractor
from agent_companion.core.vision.summarizer import OpenAIVisionSummarizer
from agent_companion.core.voice import safe_voice_line
from agent_companion.core.watch import WatchAnswerer, WatchFrame, WatchSession


@dataclass
class PendingStep:
    plan: AgentPlan
    index: int
    approval_id: str
    tool: str
    arguments_hash: str
    request_override: ToolRequest | None = None
    created_at: float = 0.0


class AgentCompanionApp:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.root = self.workspace / "agent_companion"
        self.character: CharacterHarness = load_character(self.root / "config" / "default_character.yaml")
        self.expression = ExpressionEngine(self.workspace, self.character)
        self.bus = EventBus(self.workspace / "data" / "agent_companion" / "events.jsonl")
        self.memory = MemoryStore(self.workspace / "data" / "agent_companion" / "memory.sqlite3")
        self.policy = PolicyGate()
        self.tools = ToolRegistry()
        self.watch_session = WatchSession()
        self.semantic_selection = SemanticTargetSelectionStore()
        self.pending_steps: dict[str, PendingStep] = {}
        self.approval_history: dict[str, PendingStep] = {}
        self.resolved_approval_ids: set[str] = set()
        self.approval_ttl_seconds = 120.0
        self._register_tools()

    def handle_user_text(self, text: str) -> list[AgentEvent]:
        selection = _parse_candidate_selection(text)
        if selection is not None and self.semantic_selection.has_pending():
            plan = AgentPlan(
                task_id=f"task-{uuid.uuid4().hex[:10]}",
                user_text=" ".join((text or "").strip().split()),
                intent="semantic_target_selection",
                steps=[ToolRequest("vision.select_target", {"selection": selection}, "根据上一次候选列表选择目标，继续进入点击确认。")],
            )
        else:
            plan = build_plan(text)
        self._emit(
            AgentEvent(
                EventType.USER_MESSAGE,
                plan.task_id,
                DisplayCard("用户请求", plan.user_text),
                safe_voice_line("我收到了。", sprite="1"),
                {"intent": plan.intent},
            ),
            plan.user_text,
        )
        if plan.intent != "companion_chat":
            self._emit(
                AgentEvent(
                    EventType.PLAN_CREATED,
                    plan.task_id,
                    DisplayCard("计划", f"识别为：{self._intent_label(plan.intent)}", self._plan_body(plan)),
                    safe_voice_line("我整理了一下步骤。", sprite="3"),
                    {"steps": [step.name for step in plan.steps]},
                ),
                plan.user_text,
            )
        self._run_plan(plan, 0)
        return self.bus.drain()

    def select_semantic_target(self, selection_id: str, rank: int) -> list[AgentEvent]:
        selection_id = (selection_id or "").strip()
        rank = _safe_rank(rank)
        plan = AgentPlan(
            task_id=f"task-{uuid.uuid4().hex[:10]}",
            user_text=f"选择候选 {rank}" if rank else "选择候选",
            intent="semantic_target_selection",
            steps=[
                ToolRequest(
                    "vision.select_target",
                    {"selection_id": selection_id, "selection": rank},
                    "根据指定候选上下文选择目标，继续进入点击确认。",
                )
            ],
        )
        self._emit(
            AgentEvent(
                EventType.USER_MESSAGE,
                plan.task_id,
                DisplayCard("用户请求", plan.user_text),
                safe_voice_line("我收到了。", sprite="1"),
                {"intent": plan.intent},
            ),
            plan.user_text,
        )
        self._emit(
            AgentEvent(
                EventType.PLAN_CREATED,
                plan.task_id,
                DisplayCard("计划", f"识别为：{self._intent_label(plan.intent)}", self._plan_body(plan)),
                safe_voice_line("我整理了一下步骤。", sprite="3"),
                {"steps": [step.name for step in plan.steps]},
            ),
            plan.user_text,
        )
        self._run_plan(plan, 0)
        return self.bus.drain()

    def resolve_approval(self, approval_id: str, approved: bool) -> list[AgentEvent]:
        if not approval_id:
            return self.bus.drain()
        if approval_id in self.resolved_approval_ids:
            self._emit_duplicate_approval_audit(approval_id)
            return self.bus.drain()
        pending = self.pending_steps.pop(approval_id, None)
        if pending is None:
            self._emit_duplicate_approval_audit(approval_id)
            return self.bus.drain()
        self.resolved_approval_ids.add(approval_id)
        if self._pending_step_expired(pending):
            state = {"intent": pending.plan.intent, "approval_expired": True, "approval_id": approval_id}
            self._attach_audit_state(state, [self._approval_lifecycle_audit(pending, "expired", "Approval expired before execution.")] if self._is_computer_pending(pending) else [])
            if self._is_codex_permission_pending(pending):
                state["codex_run"] = codex_cancel_run_state("expired")
            self._emit(
                AgentEvent(
                    EventType.TASK_FAILED,
                    pending.plan.task_id,
                    DisplayCard(
                        "Codex 权限确认已过期" if self._is_codex_permission_pending(pending) else "审批已过期",
                        "Codex 权限确认已经过期，我没有继续执行。" if self._is_codex_permission_pending(pending) else "这次确认已经过期，我没有继续执行。",
                        status="failed",
                    ),
                    safe_voice_line("这次确认已经过期，我没有继续执行。", sprite="4"),
                    state,
                ),
                pending.plan.user_text,
            )
            return self.bus.drain()
        if not approved:
            state = {"intent": pending.plan.intent, "cancelled": True, "approval_id": approval_id}
            self._attach_audit_state(state, [self._approval_lifecycle_audit(pending, "denied", "Approval was denied; no action ran.")] if self._is_computer_pending(pending) else [])
            if self._is_codex_permission_pending(pending):
                state["codex_run"] = codex_cancel_run_state("denied")
            self._emit(
                AgentEvent(
                    EventType.TASK_FAILED,
                    pending.plan.task_id,
                    DisplayCard(
                        "Codex 权限已拒绝" if self._is_codex_permission_pending(pending) else "任务已取消",
                        "你拒绝了 Codex 权限请求，我没有继续执行。" if self._is_codex_permission_pending(pending) else "你拒绝了这一步，我没有继续执行。",
                        status="failed",
                    ),
                    safe_voice_line("好，我先停在这里。", sprite="1"),
                    state,
                ),
                pending.plan.user_text,
            )
            return self.bus.drain()
        if not self._pending_step_matches(pending):
            state = {"intent": pending.plan.intent, "approval_id": approval_id, "approval_mismatch": True}
            self._attach_audit_state(state, [self._approval_lifecycle_audit(pending, "expired", "Approval no longer matched the pending action.")] if self._is_computer_pending(pending) else [])
            if self._is_codex_permission_pending(pending):
                state["codex_run"] = codex_cancel_run_state("mismatch")
            self._emit(
                AgentEvent(
                    EventType.TASK_FAILED,
                    pending.plan.task_id,
                    DisplayCard("审批已失效", "这次确认和待执行步骤不匹配，我没有继续执行。", status="failed"),
                    safe_voice_line("这次确认已经失效，我没有继续执行。", sprite="4"),
                    state,
                ),
                pending.plan.user_text,
            )
            return self.bus.drain()
        self._emit_computer_audit([self._approval_lifecycle_audit(pending, "approved", "Approval was accepted; action may run.")], pending.plan.user_text)
        self._run_plan(pending.plan, pending.index, approved_step=pending)
        return self.bus.drain()

    def _run_plan(self, plan: AgentPlan, start_index: int, approved_step: PendingStep | None = None) -> None:
        final_ok = True
        pending_approval = False
        for index, plan_step in enumerate(plan.steps[start_index:], start=start_index):
            step = self._step_for_execution(plan, index, plan_step, approved_step)
            is_approved_step = self._is_approved_step(plan, index, step, approved_step)
            decision = self.policy.classify(step, approved=is_approved_step)
            if decision.requires_approval:
                pending = self._make_pending_step(plan, index, step)
                self._store_pending_step(pending)
                agent_state = {
                    "policy": self.policy.public_payload(step),
                    "risk": decision.risk.value,
                    "approval": {
                        "approval_id": pending.approval_id,
                        "task_id": plan.task_id,
                        "step_index": index,
                        "tool": step.name,
                        "arguments_hash": pending.arguments_hash,
                    },
                }
                self._attach_audit_state(
                    agent_state,
                    [
                        computer_approval_audit_event(
                            plan.task_id,
                            step,
                            decision.risk,
                            pending.approval_id,
                            "pending",
                            "Approval is required before this Computer Use action can run.",
                        )
                    ]
                    if step.name.startswith("computer.")
                    else [],
                )
                self._emit(
                    AgentEvent(
                        EventType.APPROVAL_REQUIRED,
                        plan.task_id,
                        DisplayCard("需要确认", self._approval_summary(step), step.reason, status="approval"),
                        safe_voice_line("这一步需要你确认后我再执行。", sprite="4"),
                        agent_state,
                    ),
                    plan.user_text,
                )
                final_ok = False
                pending_approval = True
                break
            if step.name != "companion.chat":
                self._emit(
                    AgentEvent(
                        EventType.TOOL_STARTED,
                        plan.task_id,
                        DisplayCard("执行中", f"正在执行：{self._tool_label(step.name)}"),
                        safe_voice_line("我开始执行这一步。", sprite="3"),
                        {"tool": step.name},
                    ),
                    plan.user_text,
                )
            try:
                result = self.tools.run(step)
            except Exception as exc:
                result = ToolResult(
                    ok=False,
                    agent_state={"tool": step.name, "error": type(exc).__name__, "detail": str(exc)[:500]},
                    display_card=DisplayCard("工具失败", f"{step.name} 没有跑通。", str(exc)[:1800], status="failed"),
                    voice_line=safe_voice_line("这个工具没有跑通，细节在卡片里。", sprite="4"),
                )
            if result.requires_approval:
                pending_request = self._approval_request_from_result(result)
                if pending_request is not None:
                    pending = self._make_pending_step(plan, index, pending_request, request_override=pending_request)
                    self._store_pending_step(pending)
                    agent_state = {
                        "tool": result.agent_state.get("tool"),
                        "selection_id": result.agent_state.get("selection_id"),
                        "policy": self.policy.public_payload(pending_request),
                        "risk": result.risk.value,
                        "approval": {
                            "approval_id": pending.approval_id,
                            "task_id": plan.task_id,
                            "step_index": index,
                            "tool": pending_request.name,
                            "arguments_hash": pending.arguments_hash,
                        },
                        "selected_rank": result.agent_state.get("selected_rank"),
                        "target_candidate": result.agent_state.get("target_candidate"),
                        "target_candidates": result.agent_state.get("target_candidates"),
                    }
                    if "codex_run" in result.agent_state:
                        agent_state["codex_run"] = result.agent_state["codex_run"]
                    audit_entries = target_grounding_audit_events(plan.task_id, result, result.risk)
                    if pending_request.name.startswith("computer."):
                        audit_entries.append(
                            computer_approval_audit_event(
                                plan.task_id,
                                pending_request,
                                result.risk,
                                pending.approval_id,
                                "pending",
                                "Approval is required before this Computer Use action can run.",
                                result.display_card.artifacts,
                            )
                        )
                    self._attach_audit_state(agent_state, audit_entries)
                    self._emit(
                        AgentEvent(
                            EventType.APPROVAL_REQUIRED,
                            plan.task_id,
                            DisplayCard("需要确认", result.display_card.summary, result.display_card.body, status="approval", artifacts=result.display_card.artifacts),
                            result.voice_line,
                            agent_state,
                        ),
                        plan.user_text,
                    )
                    final_ok = False
                    pending_approval = True
                    break
            self._record_semantic_selection(plan, result)
            self._attach_result_audit(plan, step, result)
            self._emit_result(plan.task_id, result, plan.user_text)
            self._record_watch_context(plan, step, result)
            final_ok = final_ok and result.ok
            self.memory.remember(
                "task_result",
                f"{plan.intent}: {result.display_card.summary}",
                ephemeral=self._is_ephemeral_result(plan, step, result),
                sensitive=self._is_sensitive_result(plan, step, result),
            )
            if not result.ok:
                break
        if final_ok:
            if self._should_emit_task_completion(plan.intent):
                self._emit(
                    AgentEvent(
                        EventType.TASK_COMPLETED,
                        plan.task_id,
                        DisplayCard("任务完成", "这轮任务已经处理完。", status="success"),
                        safe_voice_line(self.character.voice.get("done", "做完了。"), sprite="5"),
                        {"intent": plan.intent},
                    ),
                    plan.user_text,
                )
        elif not pending_approval:
            self._emit(
                AgentEvent(
                    EventType.TASK_FAILED,
                    plan.task_id,
                    DisplayCard("任务未完成", "这轮任务没有跑通，细节在任务卡里。", status="failed"),
                    safe_voice_line(self.character.voice.get("failed", "没有跑通。"), sprite="4"),
                    {"intent": plan.intent},
                ),
                plan.user_text,
            )

    def _emit_result(self, task_id: str, result: ToolResult, user_text: str = "") -> None:
        event_type = EventType.TOOL_COMPLETED if result.ok else EventType.TOOL_FAILED
        self._emit(
            AgentEvent(
                event_type,
                task_id,
                result.display_card,
                result.voice_line,
                result.agent_state,
            ),
            user_text,
        )

    def _emit(self, event: AgentEvent, user_text: str = "") -> None:
        self.bus.emit(self.expression.express(event, user_text))

    @staticmethod
    def _tool_label(name: str) -> str:
        labels = {
            "companion.chat": "角色对话",
            "codex.run": "写码任务",
            "game.ok_ww.run": "游戏自动化",
            "browser.search": "浏览器搜索",
            "browser.observe": "网页观察",
            "observe.screen": "画面观察",
            "vision.resolve_target": "目标定位",
            "vision.select_target": "候选选择",
            "watch.recall": "陪看追问",
            "computer.click": "电脑点击",
            "computer.type_text": "电脑输入",
            "computer.scroll": "电脑滚动",
            "computer.hotkey": "快捷键",
            "mcp.list_tools": "工具清单",
            "files.read": "文件读取",
            "runtime.update_config": "运行设置",
        }
        return labels.get(name, "工具任务")

    @staticmethod
    def _intent_label(intent: str) -> str:
        labels = {
            "companion_chat": "日常对话",
            "coding": "写码",
            "game_assist": "游戏",
            "watch_together": "陪看",
            "watch_followup": "陪看追问",
            "browser": "浏览器",
            "computer_use": "电脑操作",
            "semantic_target": "目标定位",
            "semantic_target_selection": "候选选择",
        }
        return labels.get(intent, intent)

    @staticmethod
    def _should_emit_task_completion(intent: str) -> bool:
        return intent not in {"companion_chat", "watch_together", "watch_followup", "semantic_target", "semantic_target_selection"}

    @staticmethod
    def _is_ephemeral_result(plan: AgentPlan, step: ToolRequest, result: ToolResult) -> bool:
        if plan.intent in {"watch_together", "watch_followup"}:
            return True
        if plan.intent in {"semantic_target", "semantic_target_selection"}:
            return True
        return step.name in {"observe.screen", "watch.recall", "vision.resolve_target", "vision.select_target"}

    @staticmethod
    def _is_sensitive_result(plan: AgentPlan, step: ToolRequest, result: ToolResult) -> bool:
        return step.name.startswith("computer.") and bool(result.display_card.artifacts)

    def _plan_body(self, plan: AgentPlan) -> str:
        return "\n".join(self._tool_label(step.name) for step in plan.steps)

    def _approval_summary(self, step: ToolRequest) -> str:
        if step.name == "game.ok_ww.run":
            return "启动游戏自动化前需要你确认。"
        if step.name == "codex.run":
            return "交给 Codex 执行前需要你确认。"
        if step.name == "runtime.update_config":
            return "更新运行设置前需要你确认。"
        if step.name.startswith("computer."):
            return "操作当前电脑前需要你确认。"
        return f"{self._tool_label(step.name)}需要你确认。"

    def _register_tools(self) -> None:
        app_config = self._load_runtime_config()
        ocr = self._build_ocr_extractor(app_config)
        post_action_settle_ms = app_config.computer_use.post_action_settle_ms if app_config else 200
        self.tools.register(CompanionChatTool(self.workspace))
        self.tools.register(CodexTool(self.workspace))
        self.tools.register(BrowserTool(self.workspace, "browser.search"))
        self.tools.register(BrowserTool(self.workspace, "browser.observe"))
        self.tools.register(
            ScreenObserveTool(
                self.workspace,
                summarizer=self._build_vision_summarizer(app_config),
                ocr=ocr,
            )
        )
        self.tools.register(
            WatchRecallTool(
                self.workspace,
                self.watch_session.recent,
                WatchAnswerer(self.workspace, self.character.name, self.character.persona),
            )
        )
        self.tools.register(SemanticTargetTool(self.workspace, ocr=ocr))
        self.tools.register(SemanticTargetSelectionTool(self.semantic_selection))
        for name, action_type in (
            ("computer.click", "click"),
            ("computer.type_text", "type_text"),
            ("computer.scroll", "scroll"),
            ("computer.hotkey", "hotkey"),
        ):
            self.tools.register(
                ComputerActionTool(
                    self.workspace,
                    name,
                    action_type,
                    ocr=ocr,
                    post_action_settle_ms=post_action_settle_ms,
                )
            )
        self.tools.register(OkWwTool(self.workspace))
        self.tools.register(McpListTool(self.workspace))
        self.tools.register(FileReadTool(self.workspace))
        self.tools.register(RuntimeConfigUpdateTool(self.workspace))

    def _load_runtime_config(self) -> AppConfig | None:
        config_path = self.workspace / "config.yaml"
        if not config_path.is_file():
            return None
        try:
            return load_app_config(config_path)
        except Exception:
            return None

    def _build_vision_summarizer(self, config: AppConfig | None = None) -> OpenAIVisionSummarizer | None:
        config = config or self._load_runtime_config()
        if config is None:
            return None
        if not config.llm.is_vision_configured:
            return None
        router = ModelRouter(config.llm)
        endpoint = router.resolve("vision")
        return OpenAIVisionSummarizer(
            base_url=endpoint.base_url,
            model=endpoint.model,
            api_key=endpoint.api_key,
        )

    def _build_ocr_extractor(self, config: AppConfig | None = None) -> PytesseractOcrExtractor:
        config = config or self._load_runtime_config()
        if config is None:
            return PytesseractOcrExtractor()
        return PytesseractOcrExtractor(timeout_seconds=config.ocr.timeout_seconds)

    def _make_pending_step(self, plan: AgentPlan, index: int, step: ToolRequest, request_override: ToolRequest | None = None) -> PendingStep:
        return PendingStep(
            plan=plan,
            index=index,
            approval_id=f"approval-{uuid.uuid4().hex[:12]}",
            tool=step.name,
            arguments_hash=_arguments_hash(step.arguments),
            request_override=request_override,
            created_at=time.time(),
        )

    def _store_pending_step(self, pending: PendingStep) -> None:
        self.pending_steps[pending.approval_id] = pending
        self.approval_history[pending.approval_id] = pending

    def _pending_step_expired(self, pending: PendingStep) -> bool:
        return time.time() - pending.created_at > self.approval_ttl_seconds

    def _pending_step_matches(self, pending: PendingStep) -> bool:
        if pending.index < 0 or pending.index >= len(pending.plan.steps):
            return False
        step = pending.request_override or pending.plan.steps[pending.index]
        return step.name == pending.tool and _arguments_hash(step.arguments) == pending.arguments_hash

    def _is_approved_step(self, plan: AgentPlan, index: int, step: ToolRequest, pending: PendingStep | None) -> bool:
        if pending is None:
            return False
        return (
            pending.plan.task_id == plan.task_id
            and pending.index == index
            and pending.tool == step.name
            and pending.arguments_hash == _arguments_hash(step.arguments)
        )

    @staticmethod
    def _step_for_execution(plan: AgentPlan, index: int, step: ToolRequest, pending: PendingStep | None) -> ToolRequest:
        if pending is None or pending.request_override is None:
            return step
        if pending.plan.task_id == plan.task_id and pending.index == index:
            return pending.request_override
        return step

    @staticmethod
    def _approval_request_from_result(result: ToolResult) -> ToolRequest | None:
        payload = result.agent_state.get("approval_request")
        if not isinstance(payload, dict):
            return None
        tool = str(payload.get("tool") or "").strip()
        arguments = payload.get("arguments")
        if not tool or not isinstance(arguments, dict):
            return None
        reason = str(payload.get("reason") or result.display_card.summary or "")
        return ToolRequest(tool, arguments, reason)

    def _record_watch_context(self, plan: AgentPlan, step: ToolRequest, result: ToolResult) -> None:
        if plan.intent != "watch_together" or step.name != "observe.screen" or not result.ok:
            return
        state = result.agent_state
        observation = state.get("observation") if isinstance(state.get("observation"), dict) else {}
        artifacts = result.display_card.artifacts or []
        summary = str(state.get("vision_summary") or result.display_card.summary or "").strip()
        model_status = str(state.get("model_status") or "unknown")
        ocr = observation.get("ocr") if isinstance(observation.get("ocr"), dict) else {}
        ocr_regions = observation.get("ocr_regions") if isinstance(observation.get("ocr_regions"), list) else []
        ocr_text = _ocr_text_from_state(ocr)
        frame = WatchFrame(
            user_question=plan.user_text,
            summary=summary,
            title=str(observation.get("title") or ""),
            artifact=artifacts[0] if artifacts else "",
            model_status=model_status,
            ocr_summary=str(ocr.get("summary") or ""),
            ocr_text=ocr_text,
            ocr_regions=ocr_regions,
        )
        self.watch_session.add(frame)

    def _record_semantic_selection(self, plan: AgentPlan, result: ToolResult) -> None:
        state = result.agent_state
        if not state.get("candidate_selection_required"):
            return
        candidates = state.get("target_candidates") if isinstance(state.get("target_candidates"), list) else []
        observation = state.get("observation") if isinstance(state.get("observation"), dict) else {}
        artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), list) else result.display_card.artifacts
        if not candidates or not observation:
            return
        selection = PendingSemanticTargetSelection(
            task_id=plan.task_id,
            query=plan.user_text,
            target_candidates=[candidate for candidate in candidates if isinstance(candidate, dict)],
            observation=observation,
            artifacts=[str(artifact) for artifact in artifacts or []],
        )
        self.semantic_selection.save(selection)
        state["selection_id"] = selection.selection_id
        state["selection_context"] = {
            "selection_id": selection.selection_id,
            "task_id": selection.task_id,
            "expires_in_seconds": self.semantic_selection.ttl_seconds,
        }
        for candidate in selection.target_candidates:
            candidate["selection_id"] = selection.selection_id
        target_candidate = state.get("target_candidate")
        if isinstance(target_candidate, dict):
            target_candidate["selection_id"] = selection.selection_id
        state["target_candidates"] = selection.target_candidates

    def _attach_result_audit(self, plan: AgentPlan, step: ToolRequest, result: ToolResult) -> None:
        entries = target_grounding_audit_events(plan.task_id, result, result.risk)
        action_entry = computer_action_audit_event(plan.task_id, result, result.risk)
        if action_entry is not None:
            entries.append(action_entry)
        self._attach_audit_state(result.agent_state, entries)

    @staticmethod
    def _attach_audit_state(state: dict, entries: list[ComputerUseAuditEvent]) -> None:
        if not entries:
            return
        existing = state.get(COMPUTER_AUDIT_STATE_KEY)
        rows = existing if isinstance(existing, list) else []
        state[COMPUTER_AUDIT_STATE_KEY] = [*rows, *audit_state(entries)]

    def _approval_lifecycle_audit(self, pending: PendingStep, status: str, summary: str) -> ComputerUseAuditEvent:
        request = pending.request_override or pending.plan.steps[pending.index]
        risk = RiskLevel.MEDIUM if request.name.startswith("computer.") else RiskLevel.LOW
        return computer_approval_audit_event(
            pending.plan.task_id,
            request,
            risk,
            pending.approval_id,
            status,
            summary,
        )

    def _emit_duplicate_approval_audit(self, approval_id: str) -> None:
        pending = self.approval_history.get(approval_id)
        if pending is None or not self._is_computer_pending(pending):
            return
        self._emit_computer_audit([self._approval_lifecycle_audit(pending, "duplicate", "Duplicate approval response was ignored.")], pending.plan.user_text)

    def _emit_computer_audit(self, entries: list[ComputerUseAuditEvent], user_text: str = "") -> None:
        for entry in entries:
            if not entry.tool_name.startswith("computer.") and not entry.event_type.startswith(("target_", "observe")):
                continue
            self._emit(
                AgentEvent(
                    EventType.AUDIT_EVENT,
                    entry.task_id,
                    DisplayCard("Computer Use 审计", entry.sanitized_summary, status="info"),
                    safe_voice_line("审计记录已更新。", sprite="3"),
                    {COMPUTER_AUDIT_STATE_KEY: [entry.to_agent_state()]},
                ),
                user_text,
            )

    @staticmethod
    def _is_computer_pending(pending: PendingStep) -> bool:
        request = pending.request_override or pending.plan.steps[pending.index]
        return request.name.startswith("computer.")

    @staticmethod
    def _is_codex_permission_pending(pending: PendingStep) -> bool:
        request = pending.request_override or pending.plan.steps[pending.index]
        return request.name == "codex.run" and "codex_permission_hash" in request.arguments


def _arguments_hash(arguments: dict) -> str:
    serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _ocr_text_from_state(ocr: dict) -> list[str]:
    blocks = ocr.get("text_blocks") if isinstance(ocr, dict) else []
    if not isinstance(blocks, list):
        return []
    rows: list[str] = []
    for block in blocks[:12]:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "").strip()
        if text:
            rows.append(text[:160])
    return rows


def _parse_candidate_selection(text: str) -> int | None:
    value = " ".join((text or "").strip().split())
    if not value:
        return None
    if "就这个" in value or "就它" in value or "这个吧" in value:
        return 1
    import re

    match = re.search(r"(?:选|选择|点|点击)?\s*第?\s*(\d{1,2})\s*(?:个|项|号)?", value)
    if match and any(token in value for token in ("选", "选择", "第", "个", "项", "号", "点", "点击")):
        return int(match.group(1))
    chinese_digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
    }
    for token, number in chinese_digits.items():
        if f"第{token}个" in value or f"选{token}" in value or f"点第{token}" in value:
            return number
    return None


def _safe_rank(value: object) -> int:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return 0
    return rank if rank > 0 else 0
