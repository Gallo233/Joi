"""Every integration registers under the same rules, and it is checked.

TDD §11.1 and PRD-SKL-001 require Codex, browser, MCP, files and game to share
one registration contract: id, schema, policy, dry-run, status and audit. The
facts live in three modules keyed by tool name, so the failure mode is drift --
rename a tool in one table and it keeps working while quietly resolving to
`joi.unknown`, which detaches it from its Skill's on/off switch and downgrades
its effect to a fallback. These tests make the live registry prove otherwise.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agent_companion.core.action_intent import ActionIntent, EffectKind
from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.integration_contract import (
    CONSENT_AT_MODE_ENTRY,
    TOOL_CONTRACTS,
    contract_for,
    contract_violations,
)
from agent_companion.core.policy import PolicyGate
from agent_companion.core.schemas import ToolRequest
from agent_companion.core.skill_manifest import UNKNOWN_SKILL_ID, skill_id_for_tool


class IntegrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.app = AgentCompanionApp(self.workspace)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def registered_tools(self) -> list[str]:
        names = sorted(self.app.tools._tools)
        self.assertTrue(names, "expected the app to register tools")
        return names

    def test_the_three_registration_sources_agree(self) -> None:
        violations = contract_violations()
        self.assertEqual(violations, [], "registration contract drift:\n" + "\n".join(violations))

    def test_every_registered_tool_has_a_contract(self) -> None:
        missing = [name for name in self.registered_tools() if contract_for(name) is None]
        self.assertEqual(
            missing,
            [],
            "these tools are registered but declare no skill, effect or risk: " + ", ".join(missing),
        )

    def test_no_registered_tool_falls_back_to_an_unknown_skill(self) -> None:
        orphans = [name for name in self.registered_tools() if skill_id_for_tool(name) == UNKNOWN_SKILL_ID]
        self.assertEqual(
            orphans,
            [],
            "a tool bound to joi.unknown escapes its skill toggle: " + ", ".join(orphans),
        )

    def test_every_contracted_tool_is_actually_registered(self) -> None:
        """A contract for a tool nobody registers is a stale name."""

        registered = set(self.registered_tools())
        # Effects and risks are also declared for capabilities Joi does not
        # implement yet, but a *skill-bound* tool is one it claims to offer.
        stale = sorted(name for name in TOOL_CONTRACTS if name not in registered)
        self.assertEqual(stale, [], "declared by a skill but never registered: " + ", ".join(stale))

    def test_disabling_a_skill_blocks_every_tool_that_skill_owns(self) -> None:
        for name, contract in sorted(TOOL_CONTRACTS.items()):
            with self.subTest(tool=name):
                gate = PolicyGate(disabled_skills={contract.skill_id})
                decision = gate.classify(ToolRequest(name, {}), approved=True)
                self.assertFalse(decision.allowed, f"{name} ran with {contract.skill_id} disabled")
                self.assertEqual(decision.reason, "skill_disabled")

    def test_an_enabled_skill_does_not_block_its_own_tools(self) -> None:
        for name, contract in sorted(TOOL_CONTRACTS.items()):
            with self.subTest(tool=name):
                gate = PolicyGate(disabled_skills={"joi.memory"} - {contract.skill_id})
                decision = gate.classify(ToolRequest(name, {}), approved=True)
                self.assertTrue(decision.allowed, f"{name} was blocked while its skill was enabled")

    def test_a_low_risk_tool_either_changes_nothing_or_was_agreed_at_mode_entry(self) -> None:
        """Nothing that touches the outside world runs on nobody's say-so.

        Low risk means the policy gate will not stop to ask. That is only
        acceptable when there is nothing to ask about, or when the user already
        answered by choosing the mode -- and the second case has to be declared.
        """

        for name, contract in sorted(TOOL_CONTRACTS.items()):
            if contract.risk != "low":
                continue
            with self.subTest(tool=name):
                self.assertIn(
                    contract.consent,
                    {"none", "mode_entry"},
                    f"{name} changes {contract.effect.value} outside Joi and nothing confirms it",
                )
                if contract.consent == "mode_entry":
                    self.assertIn(name, CONSENT_AT_MODE_ENTRY)

    def test_mode_entry_consent_is_only_claimed_by_tools_no_planner_can_emit(self) -> None:
        """A planner-reachable tool cannot borrow the user's mode selection."""

        planner_sources = [
            Path("agent_companion/core/planner.py"),
            Path("agent_companion/core/llm_planner.py"),
        ]
        for name in sorted(CONSENT_AT_MODE_ENTRY):
            for source in planner_sources:
                with self.subTest(tool=name, source=source.name):
                    self.assertNotIn(
                        f'"{name}"',
                        source.read_text(encoding="utf-8"),
                        f"{source.name} can plan {name}, so mode entry is not where consent happens",
                    )

    def test_a_red_line_tool_is_never_agreed_in_advance(self) -> None:
        for name, contract in sorted(TOOL_CONTRACTS.items()):
            if not contract.is_red_line:
                continue
            with self.subTest(tool=name):
                self.assertEqual(contract.consent, "per_call")
                self.assertEqual(contract.risk, "high")

    def test_effect_classification_matches_the_contract(self) -> None:
        """The gate and the contract must read a bare request the same way."""

        for name, contract in sorted(TOOL_CONTRACTS.items()):
            with self.subTest(tool=name):
                intent = ActionIntent.from_request(ToolRequest(name, {}))
                self.assertEqual(intent.typed_effect, contract.effect)

    def test_every_contract_names_an_audit_channel(self) -> None:
        for name, contract in sorted(TOOL_CONTRACTS.items()):
            with self.subTest(tool=name):
                self.assertTrue(contract.audit, f"{name} declares no audit channel")
                self.assertTrue(contract.state_policy, f"{name} declares no state policy")


if __name__ == "__main__":
    unittest.main()
