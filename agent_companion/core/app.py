from __future__ import annotations

from pathlib import Path

from agent_companion.core.character import CharacterHarness, load_character
from agent_companion.core.event_bus import EventBus
from agent_companion.core.memory import MemoryStore
from agent_companion.core.planner import build_plan
from agent_companion.core.policy import PolicyGate
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, ToolResult
from agent_companion.core.tools.browser import BrowserTool
from agent_companion.core.tools.codex import CodexTool
from agent_companion.core.tools.files import FileReadTool
from agent_companion.core.tools.game_ok_ww import OkWwTool
from agent_companion.core.tools.mcp import McpListTool
from agent_companion.core.tools.registry import ToolRegistry
from agent_companion.core.voice import safe_voice_line


class AgentCompanionApp:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.root = self.workspace / "agent_companion"
        self.character: CharacterHarness = load_character(self.root / "config" / "default_character.yaml")
        self.bus = EventBus(self.workspace / "data" / "agent_companion" / "events.jsonl")
        self.memory = MemoryStore(self.workspace / "data" / "agent_companion" / "memory.sqlite3")
        self.policy = PolicyGate()
        self.tools = ToolRegistry()
        self._register_tools()

    def handle_user_text(self, text: str, approved: bool = False) -> list[AgentEvent]:
        plan = build_plan(text)
        self.bus.emit(
            AgentEvent(
                EventType.USER_MESSAGE,
                plan.task_id,
                DisplayCard("用户请求", plan.user_text),
                safe_voice_line("我收到了。", sprite="1"),
                {"intent": plan.intent},
            )
        )
        self.bus.emit(
            AgentEvent(
                EventType.PLAN_CREATED,
                plan.task_id,
                DisplayCard("计划", f"识别为：{plan.intent}", "\n".join(step.name for step in plan.steps)),
                safe_voice_line("我整理了一下步骤。", sprite="3"),
                {"steps": [step.name for step in plan.steps]},
            )
        )
        final_ok = True
        for step in plan.steps:
            decision = self.policy.classify(step, approved=approved)
            if decision.requires_approval:
                self.bus.emit(
                    AgentEvent(
                        EventType.APPROVAL_REQUIRED,
                        plan.task_id,
                        DisplayCard("需要确认", f"{step.name} 需要你确认。", step.reason, status="approval"),
                        safe_voice_line("这一步需要你确认后我再执行。", sprite="4"),
                        {"policy": self.policy.public_payload(step), "risk": decision.risk.value},
                    )
                )
                final_ok = False
                break
            self.bus.emit(
                AgentEvent(
                    EventType.TOOL_STARTED,
                    plan.task_id,
                    DisplayCard("执行中", f"正在执行：{step.name}"),
                    safe_voice_line("我开始执行这一步。", sprite="3"),
                    {"tool": step.name},
                )
            )
            result = self.tools.run(step)
            self._emit_result(plan.task_id, result)
            final_ok = final_ok and result.ok
            self.memory.remember("task_result", f"{plan.intent}: {result.display_card.summary}")
            if not result.ok:
                break
        if final_ok:
            self.bus.emit(
                AgentEvent(
                    EventType.TASK_COMPLETED,
                    plan.task_id,
                    DisplayCard("任务完成", "这轮任务已经处理完。", status="success"),
                    safe_voice_line(self.character.voice.get("done", "做完了。"), sprite="5"),
                    {"intent": plan.intent},
                )
            )
        return self.bus.drain()

    def _emit_result(self, task_id: str, result: ToolResult) -> None:
        event_type = EventType.TOOL_COMPLETED if result.ok else EventType.TOOL_FAILED
        self.bus.emit(
            AgentEvent(
                event_type,
                task_id,
                result.display_card,
                result.voice_line,
                result.agent_state,
            )
        )

    def _register_tools(self) -> None:
        self.tools.register(CodexTool(self.workspace))
        self.tools.register(BrowserTool(self.workspace, "browser.search"))
        self.tools.register(BrowserTool(self.workspace, "browser.observe"))
        self.tools.register(BrowserTool(self.workspace, "observe.screen"))
        self.tools.register(OkWwTool(self.workspace))
        self.tools.register(McpListTool(self.workspace))
        self.tools.register(FileReadTool(self.workspace))

