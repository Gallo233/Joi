from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
import unittest

from agent_companion.core.minecraft_autonomy import (
    MinecraftAutonomyTicker,
    _build_prompt,
    _parse_decision,
)
from agent_companion.core.server import (
    AUTONOMY_SPEECH_HOLD_SECONDS,
    AUTONOMY_VOICE_RUN_ID,
    JsonRpcBridge,
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

    def test_a_proactive_line_is_written_in_the_chat_language(self) -> None:
        """A Japanese-named character answered a Chinese session in Japanese.

        Autonomy writes straight into the chat and the character voice, and had
        no language rule anywhere in its prompt or downstream of it.
        """

        from agent_companion.core.minecraft_autonomy import _build_prompt

        prompt = _build_prompt({"ok": True, "language": "中文", "observation": {}})
        self.assertIn("台词必须用中文书写", prompt)
        self.assertNotIn("台词必须用书写", _build_prompt({"ok": True, "observation": {}}))

    def test_joi_can_still_talk_while_she_is_busy_but_not_start_a_second_action(self) -> None:
        """A minutes-long goal used to make her completely silent for its duration."""

        from agent_companion.core.minecraft_autonomy import _build_prompt

        self.assertIn("只能 speak", _build_prompt({"ok": True, "busy": True, "observation": {}}))
        harness = _Harness(
            context={"ok": True, "busy": True, "observation": {}},
            decisions=[{"kind": "propose", "intent": {"action": "observe"}}, {"kind": "speak", "text": "橡木快挖完了"}],
        )
        harness.ticker._tick("session-busy")
        self.assertEqual(harness.proposed, [])
        harness.ticker._tick("session-busy")
        self.assertEqual(harness.spoken, ["橡木快挖完了"])

    def test_what_she_is_carrying_reaches_the_prompt(self) -> None:
        """Without item names she had nothing constructive to propose."""

        from agent_companion.core.minecraft_autonomy import _build_prompt

        prompt = _build_prompt({"ok": True, "observation": {"inventory_items": [{"name": "oak_log", "count": 7}]}})
        self.assertIn("背包：oak_log×7", prompt)
        self.assertIn("背包：空", _build_prompt({"ok": True, "observation": {}}))

    def test_interval_is_clamped_and_status_reports_it(self) -> None:
        harness = _Harness(interval_seconds=1)
        self.assertEqual(harness.ticker.interval_seconds, 10.0)
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


class SpeechGuardTests(unittest.TestCase):
    """Joi has to be able to speak more than once.

    A voice generation is only retired on barge-in, cancel or a character
    switch, so "a generation exists" stays true for the rest of the session.
    Guarding on that silenced every proactive line after the first turn -- and
    a proactive line, which starts a generation of its own, silenced itself.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        (self.workspace / "config.yaml").write_text(
            "characters:\n  - name: 测试角色\n    setting: 测试\n", encoding="utf-8"
        )
        self.bridge = JsonRpcBridge(self.workspace)
        self.thread_id = str(self.bridge.collaboration.context().get("thread_id") or "")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_a_fresh_user_turn_holds_the_channel(self) -> None:
        self.bridge.app.voice_generations.begin(self.thread_id, character_id="c", run_id="user-turn")
        self.assertTrue(self.bridge._autonomy_speech_guard())

    def test_an_old_user_turn_no_longer_holds_it(self) -> None:
        generation = self.bridge.app.voice_generations.begin(self.thread_id, character_id="c", run_id="user-turn")
        aged = replace(generation, created_at=time.time() - AUTONOMY_SPEECH_HOLD_SECONDS - 1)
        self.bridge.app.voice_generations._current[self.thread_id or "__global__"] = aged
        self.assertFalse(self.bridge._autonomy_speech_guard())

    def test_joi_does_not_silence_herself(self) -> None:
        self.bridge.app.voice_generations.begin(
            self.thread_id, character_id="c", run_id=AUTONOMY_VOICE_RUN_ID
        )
        self.assertFalse(self.bridge._autonomy_speech_guard())

    def test_a_proactive_line_is_marked_as_speech_not_as_work(self) -> None:
        self.bridge._autonomy_speak("那边有怪，要我去看看吗？")
        events = self.bridge.app.bus.drain()
        spoken = [event for event in events if (event.agent_state or {}).get("tool") == "minecraft.autonomy"]
        self.assertEqual(len(spoken), 1)
        self.assertEqual(spoken[0].display_card.title, "对话")
        self.assertTrue((spoken[0].agent_state or {}).get("proactive"))
        self.assertIn("要我去看看吗", spoken[0].voice_line.text)
        # And having spoken, she is still allowed to speak again.
        self.assertFalse(self.bridge._autonomy_speech_guard())


if __name__ == "__main__":
    unittest.main()
