from __future__ import annotations

import json
import time
import unittest

from agent_companion.core.minecraft_autonomy import (
    MinecraftAutonomyTicker,
    _build_prompt,
    _parse_decision,
)


class _Harness:
    def __init__(
        self,
        *,
        context: object = None,
        decisions: list[object] | None = None,
        speech_guard: bool = False,
        interval_seconds: float = 30.0,
    ) -> None:
        self.context = {"ok": True, "observation": {"dimension": "overworld", "health": 20}, "hostiles": [], "recent_event_types": [], "screen_text": "", "persona": "胆小"} if context is None else context
        self.decisions = list(decisions or [])
        self.proposed: list[tuple[str, dict[str, object]]] = []
        self.spoken: list[str] = []
        self.propose_calls = 0
        self.submit_result = {"ok": True}
        self.ticker = MinecraftAutonomyTicker(
            propose=self._propose,
            submit=self._submit,
            session_context=lambda _session_id: self.context,
            on_speak=self.spoken.append,
            speech_guard=lambda: speech_guard,
            interval_seconds=interval_seconds,
        )

    def _propose(self, prompt: str) -> object:
        self.propose_calls += 1
        return self.decisions.pop(0) if self.decisions else {"kind": "none"}

    def _submit(self, session_id: str, intent: dict[str, object]) -> dict[str, object]:
        self.proposed.append((session_id, intent))
        return self.submit_result


class MinecraftAutonomyTickerTests(unittest.TestCase):
    def test_speak_decisions_reach_the_speak_sink(self) -> None:
        harness = _Harness(decisions=[{"kind": "speak", "text": "那边有怪，要我去看看吗？"}])
        harness.ticker._tick("session-1")
        self.assertEqual(harness.spoken, ["那边有怪，要我去看看吗？"])
        self.assertEqual(harness.ticker.status("session-1")["stats"]["speaks"], 1)

    def test_speak_is_skipped_while_user_speech_owns_the_channel(self) -> None:
        harness = _Harness(decisions=[{"kind": "speak", "text": "不该抢话"}], speech_guard=True)
        harness.ticker._tick("session-1")
        self.assertEqual(harness.spoken, [])
        self.assertEqual(harness.ticker.status("session-1")["stats"]["skips"], 1)

    def test_propose_decisions_submit_canonical_intents_but_never_attack(self) -> None:
        harness = _Harness(decisions=[{"kind": "propose", "intent": {"action": "observe"}}])
        harness.ticker._tick("session-1")
        self.assertEqual(harness.proposed, [("session-1", {"action": "observe", "dimension": "overworld", "radius": 16})])
        harness = _Harness(decisions=[{"kind": "propose", "intent": {"action": "attack", "count": 1}}])
        harness.ticker._tick("session-1")
        self.assertEqual(harness.proposed, [])
        harness = _Harness(decisions=[{"kind": "propose", "intent": {"action": "explode"}}])
        harness.ticker._tick("session-1")
        self.assertEqual(harness.proposed, [])

    def test_none_and_invalid_decisions_do_nothing(self) -> None:
        for decision in ({"kind": "none"}, "not json", {"kind": "sing", "text": "hi"}):
            with self.subTest(decision=decision):
                harness = _Harness(decisions=[decision])
                harness.ticker._tick("session-1")
                self.assertEqual(harness.spoken, [])
                self.assertEqual(harness.proposed, [])

    def test_unavailable_context_skips_without_calling_the_proposer(self) -> None:
        harness = _Harness(context={"ok": False})
        harness.ticker._tick("session-1")
        self.assertEqual(harness.propose_calls, 0)
        self.assertEqual(harness.ticker.status("session-1")["stats"]["skips"], 1)

    def test_interval_is_clamped_and_status_reports_it(self) -> None:
        harness = _Harness(interval_seconds=1)
        self.assertEqual(harness.ticker.interval_seconds, 15.0)
        harness.ticker.set_interval(3600)
        self.assertEqual(harness.ticker.interval_seconds, 300.0)

    def test_start_stop_lifecycle_is_idempotent_and_threads_stop(self) -> None:
        harness = _Harness(interval_seconds=15)
        self.assertTrue(harness.ticker.start("session-1"))
        self.assertFalse(harness.ticker.start("session-1"))
        self.assertTrue(harness.ticker.status("session-1")["running"])
        harness.ticker.stop("session-1")
        self.assertFalse(harness.ticker.status("session-1")["running"])
        harness.ticker.stop("session-1")

    def test_prompt_never_contains_coordinates_and_mentions_hostiles(self) -> None:
        prompt = _build_prompt(
            {
                "observation": {"dimension": "overworld", "health": 20},
                "hostiles": [{"name": "zombie", "count": 2}],
                "recent_event_types": ["combat.started"],
                "screen_text": "画面文字：生命值 20",
                "persona": "胆小",
            }
        )
        self.assertIn("zombie×2", prompt)
        self.assertIn("combat.started", prompt)
        self.assertNotIn("position", prompt)
        self.assertNotIn('"x"', prompt)

    def test_parse_accepts_dict_and_json_string(self) -> None:
        self.assertEqual(_parse_decision({"kind": "speak", "text": "hi"})["kind"], "speak")
        self.assertEqual(_parse_decision(json.dumps({"kind": "none"}))["kind"], "none")
        self.assertEqual(_parse_decision("broken")["kind"], "none")


if __name__ == "__main__":
    unittest.main()
