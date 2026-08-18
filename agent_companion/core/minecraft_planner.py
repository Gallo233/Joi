from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from agent_companion.core.minecraft_contract import (
    MinecraftContractError,
    canonicalize_game_intent,
    estimated_world_changes,
)


MAX_PLAN_STEPS = 8
_PLAN_BLOCKED_ACTIONS = frozenset({"attack"})
_PLAN_ALLOWED_HINT = (
    "observe / inventory / observe_screen / lookup_recipe / inspect_container / locate / "
    "follow_player / come_to_player / collect / mine / craft / smelt / eat / place_blueprint / "
    "deposit / sort_inventory / equip / drop / fish / sleep / flee / guard"
)

PlanCompiler = Callable[[str], Any]


def compile_plan(goal_text: str, compile_call: PlanCompiler) -> dict[str, Any]:
    """Compile one natural-language goal into ≤8 strict GameIntent steps.

    Every step is canonicalized at the same boundary as voice proposals; a
    plan is a preview, never authority - Core re-checks scope, permissions,
    budgets and receipts per step at execution time.
    """

    text = " ".join(str(goal_text or "").split())[:500]
    if not text:
        return {"ok": False, "error": "plan_goal_empty"}
    parsed = _parse_json_object(compile_call(_plan_prompt(text)))
    if parsed is None:
        return {"ok": False, "error": "plan_compile_failed"}
    summary = _bounded_plain(parsed.get("summary"), 300)
    steps_raw = parsed.get("steps")
    if not isinstance(steps_raw, list) or not 1 <= len(steps_raw) <= MAX_PLAN_STEPS:
        return {"ok": False, "error": "plan_steps_invalid"}
    steps: list[dict[str, Any]] = []
    for raw_step in steps_raw:
        if not isinstance(raw_step, Mapping):
            return {"ok": False, "error": "plan_step_invalid"}
        try:
            intent = canonicalize_game_intent({"final": True, "source": "voice", "intent": dict(raw_step)})
        except MinecraftContractError:
            return {"ok": False, "error": "plan_step_invalid"}
        if str(intent.get("action") or "") in _PLAN_BLOCKED_ACTIONS:
            return {"ok": False, "error": "plan_attack_forbidden"}
        steps.append(intent)
    return {
        "ok": True,
        "summary": summary or "Minecraft 计划",
        "steps": steps,
        "estimated_actions": len(steps),
        "estimated_changes": sum(estimated_world_changes(step) for step in steps),
    }


def compile_single_action(command_text: str, compile_call: PlanCompiler) -> dict[str, Any] | None:
    """Compile one chat/command line into at most one GameIntent, or None."""

    text = " ".join(str(command_text or "").split())[:300]
    if not text:
        return None
    parsed = _parse_json_object(compile_call(_single_action_prompt(text)))
    if parsed is None or not isinstance(parsed.get("intent"), Mapping) or not parsed.get("intent"):
        return None
    try:
        intent = canonicalize_game_intent({"final": True, "source": "text", "intent": dict(parsed["intent"])})
    except MinecraftContractError:
        return None
    if str(intent.get("action") or "") in _PLAN_BLOCKED_ACTIONS:
        return None
    return intent


def _plan_prompt(goal_text: str) -> str:
    return "\n".join(
        [
            "把用户的 Minecraft 目标编译成严格 JSON 的步骤计划。",
            f"目标：{goal_text}",
            f"可用动作只有：{_PLAN_ALLOWED_HINT}。",
            "输出格式：{\"summary\":\"一句话摘要\",\"steps\":[{\"action\":\"...\",...每个动作自己的必填/可选字段...}]}",
            "规则：最多 8 步，按依赖顺序排列；不要输出坐标、绝对位置、权限、会话或审批字段；",
            "不要使用 attack；无法可靠计划就输出 {\"summary\":\"无法计划\",\"steps\":[]} 之外的错误也不行——steps 为空即失败；",
            "只输出 JSON，不要任何解释。",
        ]
    )


def _single_action_prompt(command_text: str) -> str:
    return "\n".join(
        [
            "把这条游戏内聊天指令编译成最多一个动作。",
            f"指令：{command_text}",
            f"可用动作只有：{_PLAN_ALLOWED_HINT}。",
            "输出格式：{\"intent\":{...}} 或 {\"intent\":null}（不是指令/无法执行时）。",
            "规则：不要输出坐标、绝对位置、权限字段；不要使用 attack；只输出 JSON。",
        ]
    )


def _parse_json_object(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        loaded = json.loads(str(raw or ""))
    except (TypeError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _bounded_plain(value: Any, limit: int) -> str:
    text = str(value or "")
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    return text.strip()[:limit]
