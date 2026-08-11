"""Audio that finished after its turn ended must never play.

Speech is slow to produce and instant to become wrong. The failure this guards
against is Joi confidently narrating work the user already cancelled, moved
past, or handed to a different character.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, VoiceLine
from agent_companion.core.voice_generation import VoiceGenerationTracker


class TrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = VoiceGenerationTracker()

    def test_a_new_turn_supersedes_the_previous_one(self) -> None:
        first = self.tracker.begin("thread-a").generation_id
        second = self.tracker.begin("thread-a").generation_id
        self.assertNotEqual(first, second)
        self.assertFalse(self.tracker.is_current(first, "thread-a"))
        self.assertTrue(self.tracker.is_current(second, "thread-a"))

    def test_conversations_do_not_supersede_each_other(self) -> None:
        a = self.tracker.begin("thread-a").generation_id
        b = self.tracker.begin("thread-b").generation_id
        self.assertTrue(self.tracker.is_current(a, "thread-a"))
        self.assertTrue(self.tracker.is_current(b, "thread-b"))

    def test_retiring_stops_everything_from_that_turn(self) -> None:
        generation = self.tracker.begin("thread-a").generation_id
        self.assertEqual(self.tracker.retire("thread-a", "cancelled"), generation)
        self.assertFalse(self.tracker.is_current(generation, "thread-a"))
        self.assertEqual(self.tracker.dropped()[-1]["reason"], "cancelled")

    def test_switching_character_retires_only_the_other_character(self) -> None:
        self.tracker.begin("thread-a", character_id="joi")
        self.tracker.begin("thread-b", character_id="hikari")
        retired = self.tracker.retire_for_character_change("hikari")
        self.assertEqual(len(retired), 1)
        self.assertIsNone(self.tracker.current("thread-a"))
        self.assertIsNotNone(self.tracker.current("thread-b"))

    def test_an_untagged_utterance_is_allowed_through(self) -> None:
        # Legacy events without a generation must not be silently muted.
        self.assertTrue(self.tracker.is_current("", "thread-a"))

    def test_dropped_diagnostics_never_carry_the_spoken_text(self) -> None:
        self.tracker.begin("thread-a")
        entry = self.tracker.drop("gen-old", "thread-a", "superseded")
        self.assertEqual(set(entry), {"generation_id", "thread_id", "reason", "at"})

    def test_drop_history_is_bounded(self) -> None:
        for index in range(250):
            self.tracker.drop(f"gen-{index}", "thread-a")
        self.assertLessEqual(len(self.tracker.dropped()), 100)

    def test_an_unknown_reason_is_normalised(self) -> None:
        self.tracker.begin("thread-a")
        self.assertEqual(self.tracker.retire("thread-a", "because") and self.tracker.dropped()[-1]["reason"], "superseded")


class AppTaggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.app = AgentCompanionApp(Path(self.temporary.name))
        self.app.bus.set_context_provider(lambda: {"thread_id": "thread-a", "project_id": "p", "character_id": "c"})

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _emit(self) -> AgentEvent:
        self.app._emit(AgentEvent(EventType.TOOL_COMPLETED, "task", DisplayCard("x", "y"), VoiceLine("好了")))
        return self.app.bus.drain()[-1]

    def test_events_carry_the_generation_of_their_turn(self) -> None:
        generation = self.app.begin_voice_generation()
        self.assertEqual(self._emit().agent_state["voice_generation"], generation)

    def test_a_new_turn_changes_the_tag(self) -> None:
        self.app.begin_voice_generation()
        first = self._emit().agent_state["voice_generation"]
        self.app.begin_voice_generation()
        self.assertNotEqual(self._emit().agent_state["voice_generation"], first)

    def test_a_user_message_opens_a_generation(self) -> None:
        self.app.handle_user_text("你好")
        self.assertTrue(self.app.voice_generations.current_id("thread-a"))


class StaleAudioIsDroppedTests(unittest.TestCase):
    """The server must not broadcast audio whose turn has ended."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.app = AgentCompanionApp(Path(self.temporary.name))
        self.app.bus.set_context_provider(lambda: {"thread_id": "thread-a"})
        self.broadcast: list[str] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _bridge(self, synth_hook=None):
        """A minimal stand-in exercising the real _synthesize_voice body."""
        from agent_companion.core.server import JsonRpcBridge

        bridge = JsonRpcBridge.__new__(JsonRpcBridge)
        bridge.app = self.app

        class Tts:
            def synthesize(inner, text, sprite, emotion, delivery=None):
                if synth_hook is not None:
                    synth_hook()
                return {"voice_audio_path": "/tmp/voice.wav"}

        bridge.tts = Tts()
        bridge._broadcast = self._record  # type: ignore[method-assign]
        bridge._voice_audio_data_url = lambda path: ""  # type: ignore[assignment]
        return bridge

    def _stream_bridge(self, chunks: list[dict], chunk_hook=None):
        """The streaming path must apply the same stale-turn policy per chunk."""
        from agent_companion.core.server import JsonRpcBridge

        bridge = JsonRpcBridge.__new__(JsonRpcBridge)
        bridge.app = self.app

        class Tts:
            supports_streaming = True

            def synthesize_stream(inner, text, emotion, delivery=None):
                for index, chunk in enumerate(chunks):
                    if chunk_hook is not None:
                        chunk_hook(index)
                    yield chunk

        bridge.tts = Tts()
        bridge._broadcast = self._record  # type: ignore[method-assign]
        return bridge

    async def _record(self, message: str) -> None:
        self.broadcast.append(message)

    def _event(self, generation: str) -> AgentEvent:
        return AgentEvent(
            EventType.TOOL_COMPLETED,
            "task",
            DisplayCard("x", "y"),
            VoiceLine("好了"),
            {"voice_generation": generation},
            thread_id="thread-a",
        )

    def test_current_audio_is_broadcast(self) -> None:
        generation = self.app.begin_voice_generation()
        asyncio.run(self._bridge()._synthesize_voice(self._event(generation)))
        self.assertEqual(len(self.broadcast), 1)
        self.assertIn(generation, self.broadcast[0])

    def test_audio_superseded_before_synthesis_is_dropped(self) -> None:
        stale = self.app.begin_voice_generation()
        self.app.begin_voice_generation()
        asyncio.run(self._bridge()._synthesize_voice(self._event(stale)))
        self.assertEqual(self.broadcast, [])
        self.assertEqual(self.app.voice_generations.dropped()[-1]["generation_id"], stale)

    def test_audio_superseded_during_synthesis_is_dropped(self) -> None:
        # The real race: the user sends another message while TTS is running.
        generation = self.app.begin_voice_generation()
        bridge = self._bridge(synth_hook=lambda: self.app.begin_voice_generation())
        asyncio.run(bridge._synthesize_voice(self._event(generation)))
        self.assertEqual(self.broadcast, [], "audio finished after its turn ended and must not play")

    def test_cancelling_the_turn_drops_its_audio(self) -> None:
        generation = self.app.begin_voice_generation()
        bridge = self._bridge(synth_hook=lambda: self.app.voice_generations.retire("thread-a", "cancelled"))
        asyncio.run(bridge._synthesize_voice(self._event(generation)))
        self.assertEqual(self.broadcast, [])

    def test_streaming_audio_is_forwarded_chunk_by_chunk(self) -> None:
        generation = self.app.begin_voice_generation()
        chunks = [
            {"voice_audio_pcm16_base64": "AQI=", "voice_audio_sequence": 0},
            {"voice_audio_final": True, "voice_audio_sequence": 1},
        ]
        asyncio.run(self._stream_bridge(chunks)._synthesize_voice(self._event(generation)))
        self.assertEqual(len(self.broadcast), 2)
        self.assertIn('"voice_audio_sequence": 0', self.broadcast[0])
        self.assertIn('"voice_audio_final": true', self.broadcast[1])

    def test_streaming_stops_before_a_chunk_from_a_superseded_turn(self) -> None:
        generation = self.app.begin_voice_generation()
        chunks = [
            {"voice_audio_pcm16_base64": "AQI=", "voice_audio_sequence": 0},
            {"voice_audio_pcm16_base64": "AwQ=", "voice_audio_sequence": 1},
        ]

        def supersede_after_first(index: int) -> None:
            if index == 1:
                self.app.begin_voice_generation()

        bridge = self._stream_bridge(chunks, supersede_after_first)
        asyncio.run(bridge._synthesize_voice(self._event(generation)))
        self.assertEqual(len(self.broadcast), 1)


class SpeakableEventPolicyTests(unittest.TestCase):
    def test_progress_and_generic_task_events_never_trigger_tts(self) -> None:
        from agent_companion.core.server import SPEAKABLE_EVENTS

        for event_type in (
            EventType.RUNTIME_STARTED,
            EventType.TOOL_STARTED,
            EventType.TASK_COMPLETED,
            EventType.TASK_FAILED,
        ):
            with self.subTest(event_type=event_type.value):
                self.assertNotIn(event_type, SPEAKABLE_EVENTS)

    def test_only_user_relevant_terminal_or_approval_events_can_speak(self) -> None:
        from agent_companion.core.server import SPEAKABLE_EVENTS

        for event_type in (
            EventType.APPROVAL_REQUIRED,
            EventType.RUNTIME_FINAL,
            EventType.RUNTIME_ERROR,
            EventType.TOOL_COMPLETED,
            EventType.TOOL_FAILED,
        ):
            with self.subTest(event_type=event_type.value):
                self.assertIn(event_type, SPEAKABLE_EVENTS)


if __name__ == "__main__":
    unittest.main()
