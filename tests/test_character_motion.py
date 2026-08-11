from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.action_intent import ActionIntent, EffectKind
from agent_companion.core.character_motion import character_motion_from_text, character_motion_payload
from agent_companion.core.planner import build_plan
from agent_companion.core.policy import PolicyGate
from agent_companion.core.schemas import RiskLevel, ToolRequest
from agent_companion.core.skill_manifest import skill_boundary_for_tool
from agent_companion.core.tools.character import CharacterPerformTool


class CharacterMotionTests(unittest.TestCase):
    def test_chinese_and_english_motion_intents_are_deterministic(self) -> None:
        self.assertEqual(character_motion_from_text("Joi，跳个舞"), "dance")
        self.assertEqual(character_motion_from_text("can you do a finger-gun?"), "finger_gun")
        self.assertEqual(character_motion_from_text("挥挥手打个招呼"), "greet")
        self.assertEqual(character_motion_from_text("今天聊点什么"), "")

    def test_planner_routes_motion_without_external_execution(self) -> None:
        plan = build_plan("来段舞")
        self.assertEqual(plan.intent, "character_motion")
        self.assertEqual(plan.steps[0].name, "character.perform")
        self.assertEqual(plan.steps[0].arguments["motion"], "dance")
        # The step must stay local: it names a motion and the language to
        # answer in, and nothing that could reach outside the app.
        self.assertEqual(set(plan.steps[0].arguments) - {"motion", "reply_language"}, set())

    def test_the_planner_records_the_language_the_user_wrote_in(self) -> None:
        """The line shown on screen follows the message, not the voice: asking
        in Chinese and being answered in Japanese is the bug this prevents."""

        self.assertEqual(build_plan("来段舞").steps[0].arguments["reply_language"], "zh")
        self.assertEqual(build_plan("踊って").steps[0].arguments["reply_language"], "ja")

    def test_motion_payload_is_bounded_and_interruptible(self) -> None:
        payload = character_motion_payload("dance", duration_ms=99_999, intensity=9, loop=True)
        self.assertEqual(payload["duration_ms"], 12_000)
        self.assertEqual(payload["intensity"], 1.0)
        self.assertTrue(payload["loop"])
        self.assertTrue(payload["interruptible"])
        self.assertIsNone(character_motion_payload("delete_everything"))

    def test_tool_emits_only_semantic_motion_state(self) -> None:
        result = CharacterPerformTool().run(ToolRequest("character.perform", {"motion": "greet"}))
        self.assertTrue(result.ok)
        self.assertEqual(result.agent_state["character_motion"]["name"], "greet")
        self.assertNotIn("path", result.agent_state["character_motion"])
        self.assertNotIn("bones", result.agent_state["character_motion"])

    def test_motion_is_low_risk_and_has_no_external_effect(self) -> None:
        request = ToolRequest("character.perform", {"motion": "happy"})
        decision = PolicyGate().classify(request)
        self.assertEqual(decision.risk, RiskLevel.LOW)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_approval)
        self.assertEqual(ActionIntent.from_request(request).effect_kind, EffectKind.NONE)
        self.assertEqual(skill_boundary_for_tool(request.name)["skill_id"], "joi.companion.chat")

    def test_full_app_event_flow_reaches_the_shell_without_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AgentCompanionApp(Path(directory))
            events = app.handle_user_text("Joi，跳个舞")
        completed = [event for event in events if event.type.value == "tool_completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].agent_state["character_motion"]["name"], "dance")
        self.assertFalse(any(event.type.value == "approval_required" for event in events))
        self.assertFalse(any(event.type.value == "task_completed" for event in events))


if __name__ == "__main__":
    unittest.main()
