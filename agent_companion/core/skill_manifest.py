"""P8 Skill Manifest — Formalize Joi's native tools as skills.

Each core capability (Codex, Browser, Computer Use, Memory, Watch, etc.)
is described as a Skill with:
  - name, version, description
  - input_schema / output_schema (JSON Schema-like dicts)
  - permission_level (auto / confirm / step_confirm)
  - dry_run support
  - capability check (is this skill available on this platform?)
  - state_policy (ephemeral / durable / mixed)

This enables the planner to reason about skills, the UI to show
skill status, and the policy gate to enforce permissions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class SkillInputSchema:
    """Describes a skill's input parameters."""
    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    properties: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "optional": self.optional,
            "properties": self.properties,
        }


@dataclass(frozen=True)
class SkillManifest:
    """Complete description of a native Joi skill."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    tool_name: str = ""  # Maps to ToolAdapter.name
    category: str = ""   # coding, vision, computer_use, memory, voice, game
    permission_level: str = "auto"  # auto, confirm, step_confirm
    dry_run: bool = False  # Supports dry-run mode
    requires_approval: bool = False
    risk_level: str = "low"  # low, medium, high
    input_schema: SkillInputSchema = field(default_factory=SkillInputSchema)
    output_keys: list[str] = field(default_factory=list)  # Keys in agent_state
    state_policy: str = "ephemeral"  # ephemeral, durable, mixed
    platform: str = "all"  # all, windows, macos, linux
    dependencies: list[str] = field(default_factory=list)  # Other skill names
    capability_check: Callable[[], bool] | None = None

    def is_available(self) -> bool:
        """Check if this skill's dependencies are met on this platform."""
        if self.capability_check:
            try:
                return self.capability_check()
            except Exception:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "tool_name": self.tool_name,
            "category": self.category,
            "permission_level": self.permission_level,
            "dry_run": self.dry_run,
            "requires_approval": self.requires_approval,
            "risk_level": self.risk_level,
            "input_schema": self.input_schema.to_dict(),
            "output_keys": self.output_keys,
            "state_policy": self.state_policy,
            "platform": self.platform,
            "dependencies": self.dependencies,
            "available": self.is_available(),
        }


class SkillRegistry:
    """Registry of all native Joi skills."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillManifest] = {}

    def register(self, skill: SkillManifest) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillManifest | None:
        return self._skills.get(name)

    def all(self) -> list[SkillManifest]:
        return list(self._skills.values())

    def available(self) -> list[SkillManifest]:
        return [s for s in self._skills.values() if s.is_available()]

    def by_category(self, category: str) -> list[SkillManifest]:
        return [s for s in self._skills.values() if s.category == category]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": len(self._skills),
            "available": len(self.available()),
            "skills": [s.to_dict() for s in self._skills.values()],
        }


def build_default_skill_registry() -> SkillRegistry:
    """Build the registry with all native Joi skills."""
    import sys

    registry = SkillRegistry()

    # --- Coding ---
    registry.register(SkillManifest(
        name="codex",
        description="通过 Codex CLI 执行工程任务：修复 bug、写代码、跑测试",
        tool_name="codex.run",
        category="coding",
        permission_level="confirm",
        requires_approval=True,
        risk_level="medium",
        input_schema=SkillInputSchema(
            required=["goal"],
            optional=["context"],
            properties={"goal": {"type": "string", "description": "任务目标"}},
        ),
        output_keys=["codex_run", "memory_candidate"],
        state_policy="durable",
        platform="all",
    ))

    # --- Vision ---
    registry.register(SkillManifest(
        name="observe_screen",
        description="截取当前窗口或全屏画面，生成视觉摘要",
        tool_name="observe.screen",
        category="vision",
        permission_level="auto",
        input_schema=SkillInputSchema(
            optional=["target", "query"],
            properties={
                "target": {"type": "string", "enum": ["active_window", "fullscreen"]},
                "query": {"type": "string"},
            },
        ),
        output_keys=["observation", "vision_summary", "ocr", "ocr_regions"],
        state_policy="ephemeral",
        platform="all",
    ))

    registry.register(SkillManifest(
        name="resolve_target",
        description="从当前画面中定位语义目标（按钮、链接等）",
        tool_name="vision.resolve_target",
        category="vision",
        permission_level="auto",
        input_schema=SkillInputSchema(
            required=["query"],
            optional=["action"],
            properties={"query": {"type": "string", "description": "目标描述"}},
        ),
        output_keys=["target_candidate", "target_candidates", "needs_clarification"],
        state_policy="ephemeral",
        platform="all",
    ))

    # --- Computer Use ---
    for action, desc, risk in [
        ("click", "点击屏幕指定位置", "medium"),
        ("double_click", "双击屏幕指定位置", "medium"),
        ("drag", "拖拽操作", "medium"),
        ("type_text", "向当前前台应用输入文字", "medium"),
        ("scroll", "滚动当前画面", "low"),
        ("hotkey", "按下系统快捷键", "medium"),
        ("open_app", "打开应用程序", "low"),
    ]:
        registry.register(SkillManifest(
            name=f"computer_{action}",
            description=desc,
            tool_name=f"computer.{action}",
            category="computer_use",
            permission_level="confirm" if risk == "medium" else "auto",
            requires_approval=(risk == "medium"),
            risk_level=risk,
            input_schema=SkillInputSchema(
                required=["x", "y"] if action in ("click", "double_click") else
                          ["text"] if action in ("type_text",) else
                          ["keys"] if action == "hotkey" else
                          ["app_name"] if action == "open_app" else [],
                optional=["button"] if action in ("click", "double_click", "drag") else [],
            ),
            output_keys=["computer_use", "post_action_verification"],
            state_policy="ephemeral",
            platform="all",
        ))

    # --- Memory ---
    registry.register(SkillManifest(
        name="memory",
        description="语义记忆存储、检索、候选审批",
        tool_name="memory.*",
        category="memory",
        permission_level="auto",
        input_schema=SkillInputSchema(
            optional=["query", "kind", "limit"],
        ),
        output_keys=["memory_candidate"],
        state_policy="durable",
        platform="all",
    ))

    # --- Watch Together ---
    registry.register(SkillManifest(
        name="watch_together",
        description="陪看模式：观察画面、转写字幕、主动评论",
        tool_name="watch.recall",
        category="watch",
        permission_level="auto",
        input_schema=SkillInputSchema(
            required=["query"],
        ),
        output_keys=["watch_context", "vision_summary"],
        state_policy="ephemeral",
        platform="all",
    ))

    # --- Browser ---
    registry.register(SkillManifest(
        name="browser_search",
        description="浏览器搜索并观察结果",
        tool_name="browser.search",
        category="browser",
        permission_level="auto",
        input_schema=SkillInputSchema(required=["query"]),
        output_keys=["observation"],
        state_policy="ephemeral",
        platform="all",
    ))

    # --- Game ---
    registry.register(SkillManifest(
        name="ok_ww",
        description="鸣潮 (Wuthering Waves) 游戏日常自动化",
        tool_name="game.ok_ww.run",
        category="game",
        permission_level="confirm",
        requires_approval=True,
        risk_level="medium",
        input_schema=SkillInputSchema(required=["intent"]),
        state_policy="ephemeral",
        platform="windows",
    ))

    # --- Desktop Workflow ---
    registry.register(SkillManifest(
        name="desktop_workflow",
        description="桌面自动操作工作流：多步 Computer Use 组合",
        tool_name="desktop_workflow.run",
        category="computer_use",
        permission_level="step_confirm",
        requires_approval=True,
        risk_level="high",
        input_schema=SkillInputSchema(required=["steps"]),
        output_keys=["computer_use", "post_action_verification"],
        state_policy="ephemeral",
        platform="all",
    ))

    # --- Chat ---
    registry.register(SkillManifest(
        name="companion_chat",
        description="普通对话，角色表达回复",
        tool_name="companion.chat",
        category="chat",
        permission_level="auto",
        input_schema=SkillInputSchema(required=["text"]),
        output_keys=["expression"],
        state_policy="ephemeral",
        platform="all",
    ))

    # --- Voice ---
    registry.register(SkillManifest(
        name="voice_transcribe",
        description="语音识别 (ASR)",
        tool_name="voice.transcribe",
        category="voice",
        permission_level="auto",
        input_schema=SkillInputSchema(required=["audio"]),
        state_policy="ephemeral",
        platform="all",
    ))

    registry.register(SkillManifest(
        name="voice_speak",
        description="语音合成 (TTS)",
        tool_name="voice.speak",
        category="voice",
        permission_level="auto",
        input_schema=SkillInputSchema(required=["text"]),
        state_policy="ephemeral",
        platform="all",
    ))

    return registry
