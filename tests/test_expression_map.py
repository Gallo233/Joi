"""The character must never look calmer than the situation is.

Users read state off the character. A smile while a session waits on them, or a
neutral face while a permission is missing, is the interface lying — and they
act on it. So the mapping is derived from real state and the model cannot
override a risk condition (TDD §8.2, PRD §13.3).
"""

from __future__ import annotations

import unittest

from agent_companion.core.expression import ExpressionEngine
from agent_companion.core.expression_map import (
    EMOTIONS,
    OVERRIDE_PRIORITY,
    expression_state_from_event,
    outranks,
    resolve_condition,
    resolve_expression,
)
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, VoiceLine


class ConditionPriorityTests(unittest.TestCase):
    def test_waiting_for_the_user_outranks_everything_else(self) -> None:
        # A late success event arriving while a session waits for approval.
        condition = resolve_condition("done", session_state="waiting_approval")
        self.assertEqual(condition, "approval")

    def test_a_paused_session_reads_as_waiting_not_as_the_last_tool_event(self) -> None:
        self.assertEqual(resolve_condition("acting", session_state="paused"), "approval")
        self.assertEqual(resolve_condition("done", session_state="paused"), "approval")

    def test_takeover_and_missing_permission_outrank_progress(self) -> None:
        self.assertEqual(resolve_condition("acting", taken_over=True), "takeover")
        self.assertEqual(resolve_condition("acting", permission_missing=True), "permission_missing")

    def test_unverified_success_is_not_success(self) -> None:
        self.assertEqual(resolve_condition("done", verified=True), "done")
        self.assertEqual(resolve_condition("done", verified=False), "failed")

    def test_ordinary_progress_maps_straightforwardly(self) -> None:
        self.assertEqual(resolve_condition("thinking"), "thinking")
        self.assertEqual(resolve_condition("understanding"), "thinking")
        self.assertEqual(resolve_condition("acting"), "acting")
        self.assertEqual(resolve_condition("received"), "received")
        self.assertEqual(resolve_condition("idle"), "idle")

    def test_priority_order_is_respected(self) -> None:
        self.assertTrue(outranks("approval", "acting"))
        self.assertTrue(outranks("permission_missing", "thinking"))
        self.assertTrue(outranks("failed", "acting"))
        self.assertFalse(outranks("acting", "approval"))
        # "done" is weaker than anything that needs the user.
        self.assertTrue(outranks("approval", "done"))


class ExpressionResolutionTests(unittest.TestCase):
    def test_every_condition_maps_into_the_known_vocabulary(self) -> None:
        for condition in OVERRIDE_PRIORITY:
            with self.subTest(condition=condition):
                intent = resolve_expression(condition if condition in {"acting", "thinking", "received", "idle", "failed"} else "acting",
                                            permission_missing=condition == "permission_missing",
                                            taken_over=condition == "takeover",
                                            session_state="waiting_approval" if condition == "approval" else "")
                self.assertIn(intent.emotion, EMOTIONS)

    def test_risk_states_are_locked_against_the_model(self) -> None:
        for kwargs in (
            {"session_state": "waiting_approval"},
            {"taken_over": True},
            {"permission_missing": True},
        ):
            with self.subTest(**kwargs):
                intent = resolve_expression("acting", **kwargs)
                self.assertTrue(intent.locked)
                # Whatever the model suggests, the risk face stays.
                for suggestion in ("happy", "neutral", "thinking", "nonsense"):
                    self.assertEqual(intent.clamp(suggestion), intent.emotion)

    def test_a_missing_permission_looks_worried_not_merely_serious(self) -> None:
        self.assertEqual(resolve_expression("acting", permission_missing=True).emotion, "worried")

    def test_the_model_may_vary_tone_only_within_a_safe_state(self) -> None:
        acting = resolve_expression("acting")
        self.assertFalse(acting.locked)
        self.assertEqual(acting.clamp("thinking"), "thinking")
        # Still cannot invent cheerfulness mid-action.
        self.assertEqual(acting.clamp("happy"), "alert")
        self.assertEqual(acting.clamp("garbage"), "alert")

    def test_verified_success_may_be_happy(self) -> None:
        done = resolve_expression("done", verified=True)
        self.assertEqual(done.emotion, "happy")
        self.assertEqual(done.clamp("neutral"), "neutral")

    def test_idle_does_not_speak(self) -> None:
        self.assertFalse(resolve_expression("idle").may_speak)
        self.assertTrue(resolve_expression("acting").may_speak)


class EventStateExtractionTests(unittest.TestCase):
    def test_a_desktop_action_without_an_after_observation_is_unverified(self) -> None:
        state = {"public_phase": "done", "computer_use": {"before_title": "Safari"}}
        self.assertFalse(expression_state_from_event(state)["verified"])
        observed = {"public_phase": "done", "computer_use": {"observation": {"title": "Safari"}}}
        self.assertTrue(expression_state_from_event(observed)["verified"])

    def test_a_likely_noop_verification_is_not_verified(self) -> None:
        state = {"public_phase": "done", "post_action_verification": {"status": "likely_noop"}}
        self.assertFalse(expression_state_from_event(state)["verified"])

    def test_a_paused_session_is_carried_through(self) -> None:
        extracted = expression_state_from_event({"public_phase": "done"}, {"state": "paused", "pause_reason": "taken_over"})
        self.assertEqual(extracted["session_state"], "paused")
        self.assertTrue(extracted["taken_over"])


class ExpressionEngineTests(unittest.TestCase):
    """End to end: the engine applies the mapping to real events."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        from agent_companion.core.character import load_character

        self.temporary = tempfile.TemporaryDirectory()
        workspace = Path(self.temporary.name)
        character = load_character(Path("agent_companion/config/default_character.yaml"))
        self.engine = ExpressionEngine(workspace, character)
        # Force the deterministic path; the mapping must hold without a model.
        self.engine._llm_expression = lambda event, user_text: None  # type: ignore[assignment]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _event(self, phase: str, emotion: str) -> AgentEvent:
        return AgentEvent(
            EventType.TOOL_COMPLETED,
            "task-1",
            DisplayCard("电脑操作", "完成"),
            VoiceLine("好了", emotion=emotion),
            {"public_phase": phase},
        )

    def test_a_late_success_cannot_repaint_a_paused_session_happy(self) -> None:
        expressed = self.engine.express(self._event("done", "happy"), session={"state": "paused"})
        self.assertEqual(expressed.voice_line.emotion, "serious")
        self.assertEqual(expressed.agent_state["expression_intent"]["condition"], "approval")

    def test_a_model_suggested_emotion_is_clamped(self) -> None:
        self.engine._llm_expression = lambda event, user_text: {"voice_text": "搞定啦", "emotion": "happy"}  # type: ignore[assignment]
        expressed = self.engine.express(self._event("acting", "neutral"))
        self.assertEqual(expressed.voice_line.emotion, "alert")

    def test_the_intent_is_published_for_the_shell(self) -> None:
        expressed = self.engine.express(self._event("acting", "neutral"))
        intent = expressed.agent_state["expression_intent"]
        self.assertEqual(intent["condition"], "acting")
        self.assertIn("alert", intent["allowed_emotions"])
        self.assertFalse(intent["locked"])


if __name__ == "__main__":
    unittest.main()
