from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_companion.core.character import CharacterHarness, load_character
from agent_companion.core.config import ModelRouter, load_app_config
from agent_companion.core.event_bus import EventBus
from agent_companion.core.expression import ExpressionEngine
from agent_companion.core.memory import MemoryStore
from agent_companion.core.planner import build_plan
from agent_companion.core.policy import PolicyGate
from agent_companion.core.schemas import AgentEvent, AgentPlan, DisplayCard, EventType, ToolRequest, ToolResult
from agent_companion.core.tools.browser import BrowserTool
from agent_companion.core.tools.chat import CompanionChatTool
from agent_companion.core.tools.codex import CodexTool
from agent_companion.core.tools.files import FileReadTool
from agent_companion.core.tools.game_ok_ww import OkWwTool
from agent_companion.core.tools.mcp import McpListTool
from agent_companion.core.tools.registry import ToolRegistry
from agent_companion.core.tools.screen_observe import ScreenObserveTool
from agent_companion.core.vision.summarizer import OpenAIVisionSummarizer
from agent_companion.core.voice import safe_voice_line


@dataclass
class PendingStep:
    plan: AgentPlan
    index: int


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
        self.pending_steps: dict[str, PendingStep] = {}
        self.resolved_approval_tasks: set[str] = set()
        self._register_tools()

    def handle_user_text(self, text: str, approved: bool = False) -> list[AgentEvent]:
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
        self._run_plan(plan, 0, approved=approved)
        return self.bus.drain()

    def resolve_approval(self, task_id: str, approved: bool) -> list[AgentEvent]:
        pending = self.pending_steps.pop(task_id, None)
        if pending is None:
            return self.bus.drain()
        if not approved:
            self.resolved_approval_tasks.add(task_id)
            self._emit(
                AgentEvent(
                    EventType.TASK_FAILED,
                    task_id,
                    DisplayCard("任务已取消", "你拒绝了这一步，我没有继续执行。", status="failed"),
                    safe_voice_line("好，我先停在这里。", sprite="1"),
                    {"intent": pending.plan.intent, "cancelled": True},
                ),
                pending.plan.user_text,
            )
            return self.bus.drain()
        self.resolved_approval_tasks.add(task_id)
        self._run_plan(pending.plan, pending.index, approved=True)
        return self.bus.drain()

    def _run_plan(self, plan: AgentPlan, start_index: int, approved: bool) -> None:
        final_ok = True
        pending_approval = False
        for index, step in enumerate(plan.steps[start_index:], start=start_index):
            decision = self.policy.classify(step, approved=approved)
            if decision.requires_approval:
                self.pending_steps[plan.task_id] = PendingStep(plan, index)
                self._emit(
                    AgentEvent(
                        EventType.APPROVAL_REQUIRED,
                        plan.task_id,
                        DisplayCard("需要确认", self._approval_summary(step), step.reason, status="approval"),
                        safe_voice_line("这一步需要你确认后我再执行。", sprite="4"),
                        {"policy": self.policy.public_payload(step), "risk": decision.risk.value},
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
            self._emit_result(plan.task_id, result, plan.user_text)
            final_ok = final_ok and result.ok
            self.memory.remember("task_result", f"{plan.intent}: {result.display_card.summary}")
            if not result.ok:
                break
        if final_ok:
            if plan.intent != "companion_chat":
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
            "mcp.list_tools": "工具清单",
            "files.read": "文件读取",
        }
        return labels.get(name, "工具任务")

    @staticmethod
    def _intent_label(intent: str) -> str:
        labels = {
            "companion_chat": "日常对话",
            "coding": "写码",
            "game_assist": "游戏",
            "watch_together": "陪看",
            "browser": "浏览器",
        }
        return labels.get(intent, intent)

    def _plan_body(self, plan: AgentPlan) -> str:
        return "\n".join(self._tool_label(step.name) for step in plan.steps)

    def _approval_summary(self, step: ToolRequest) -> str:
        if step.name == "game.ok_ww.run":
            return "启动游戏自动化前需要你确认。"
        if step.name == "codex.run":
            return "交给 Codex 执行前需要你确认。"
        return f"{self._tool_label(step.name)}需要你确认。"

    def _register_tools(self) -> None:
        self.tools.register(CompanionChatTool(self.workspace))
        self.tools.register(CodexTool(self.workspace))
        self.tools.register(BrowserTool(self.workspace, "browser.search"))
        self.tools.register(BrowserTool(self.workspace, "browser.observe"))
        self.tools.register(ScreenObserveTool(self.workspace, summarizer=self._build_vision_summarizer()))
        self.tools.register(OkWwTool(self.workspace))
        self.tools.register(McpListTool(self.workspace))
        self.tools.register(FileReadTool(self.workspace))

    def _build_vision_summarizer(self) -> OpenAIVisionSummarizer | None:
        config_path = self.workspace / "config.yaml"
        if not config_path.is_file():
            return None
        try:
            config = load_app_config(config_path)
        except Exception:
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
