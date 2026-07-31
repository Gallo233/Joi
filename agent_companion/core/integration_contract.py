"""One registration record per tool, joined from the places that define it.

Codex, browser, MCP, files and game integrations must all register under the
same rules (TDD §11.1, PRD-SKL-001), but the facts about a tool live in three
modules: which Skill owns it (`skill_manifest`), what it changes outside Joi
(`action_intent`), and how much confirmation it needs (`policy`). Each keys off
the tool's name, and nothing made them agree.

Agreeing by luck is not the same as agreeing by construction. A tool renamed in
one table and not the others keeps working -- it silently resolves to
`joi.unknown`, which means its Skill's on/off switch no longer reaches it and
its effect degrades to a guess. This module performs the join once, exposes the
result as a single record, and reports every disagreement it finds so a test
can fail on it instead of a user discovering it.

This is a description of what is already registered, not a second registry:
adding an entry here does not make a tool exist.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_companion.core.action_intent import RED_LINE_EFFECTS, TOOL_EFFECTS, EffectKind
from agent_companion.core.policy import HIGH_RISK, LOW_RISK, MEDIUM_RISK
from agent_companion.core.skill_manifest import (
    KNOWN_SKILL_IDS,
    UNKNOWN_SKILL_ID,
    declared_skill_tools,
    tool_skill_bindings,
)


# How the user agreed to a tool that changes something outside Joi.
#
# `per_call` is the default and the only one the policy gate can enforce on its
# own. `mode_entry` means the consent was captured before the request existed --
# the user picked that mode, its target and its settings, and typed into it --
# so asking again on the first step would be asking twice for one decision. It
# is listed here rather than inferred, because a tool that quietly moved to
# `mode_entry` would be an approval bypass and should be visible in review.
CONSENT_AT_MODE_ENTRY: frozenset[str] = frozenset(
    {
        # Reachable only from AgentCompanionApp.handle_agent_cli_text, which
        # runs when the user has selected Agent CLI takeover. No planner emits
        # it. Permission requests raised *by* the CLI still interrupt for
        # approval; this covers only starting the run the user asked for.
        "agent_cli.run",
    }
)


@dataclass(frozen=True)
class ToolContract:
    """Everything the runtime decides about a tool before it runs."""

    name: str
    skill_id: str
    category: str
    effect: EffectKind
    risk: str
    permission_level: str
    state_policy: str
    audit: str

    @property
    def is_red_line(self) -> bool:
        return self.effect in RED_LINE_EFFECTS

    @property
    def consent(self) -> str:
        if self.effect is EffectKind.NONE:
            return "none"
        return "mode_entry" if self.name in CONSENT_AT_MODE_ENTRY else "per_call"

    def payload(self) -> dict[str, str]:
        return {
            "name": self.name,
            "skill_id": self.skill_id,
            "category": self.category,
            "effect": self.effect.value,
            "risk": self.risk,
            "consent": self.consent,
            "permission_level": self.permission_level,
            "state_policy": self.state_policy,
            "audit": self.audit,
        }


def _risk_for(name: str) -> str:
    if name in HIGH_RISK:
        return "high"
    if name in MEDIUM_RISK:
        return "medium"
    if name in LOW_RISK:
        return "low"
    # policy.classify treats an unlisted tool as medium rather than safe; the
    # contract reports the same thing so the two cannot disagree.
    return "medium"


def build_contracts() -> dict[str, ToolContract]:
    contracts: dict[str, ToolContract] = {}
    for name, binding in sorted(tool_skill_bindings().items()):
        contracts[name] = ToolContract(
            name=name,
            skill_id=binding.get("skill_id", UNKNOWN_SKILL_ID),
            category=binding.get("category", "unknown"),
            effect=TOOL_EFFECTS.get(name, EffectKind.DESKTOP_INPUT),
            risk=_risk_for(name),
            permission_level=binding.get("permission_level", "medium"),
            state_policy=binding.get("state_policy", "ephemeral"),
            audit=binding.get("audit", "event_log"),
        )
    return contracts


TOOL_CONTRACTS: dict[str, ToolContract] = build_contracts()


def contract_for(tool_name: str) -> ToolContract | None:
    return TOOL_CONTRACTS.get(str(tool_name or ""))


def contract_violations(declared_tools: dict[str, set[str]] | None = None) -> list[str]:
    """Every way the three sources currently disagree, as readable lines."""

    declared = declared_tools if declared_tools is not None else declared_skill_tools()
    violations: list[str] = []
    for name, contract in sorted(TOOL_CONTRACTS.items()):
        if contract.skill_id not in KNOWN_SKILL_IDS:
            violations.append(f"{name}: bound to unknown skill {contract.skill_id!r}")
        elif name not in declared.get(contract.skill_id, set()):
            violations.append(f"{name}: owned by {contract.skill_id} but that skill does not declare it")
        if name not in TOOL_EFFECTS:
            violations.append(f"{name}: no declared effect, so it would be classified by fallback")
        if contract.effect is EffectKind.NONE and contract.risk != "low":
            violations.append(f"{name}: effect none but risk {contract.risk}")
        if contract.is_red_line and contract.risk != "high":
            violations.append(f"{name}: red-line effect {contract.effect.value} but risk {contract.risk}")
        if contract.risk == "low" and contract.consent == "per_call":
            violations.append(
                f"{name}: changes {contract.effect.value} outside Joi at low risk, so nothing asks the user"
            )
        if contract.is_red_line and contract.consent != "per_call":
            violations.append(f"{name}: red lines are always confirmed per call, never at mode entry")

    for skill_id, tools in sorted(declared.items()):
        for name in sorted(tools):
            if name not in TOOL_CONTRACTS:
                violations.append(f"{name}: declared by {skill_id} but has no skill binding")
    return violations
