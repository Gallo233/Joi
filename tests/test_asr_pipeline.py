from __future__ import annotations

import asyncio
import base64
import threading
import unittest

from agent_companion.core.server import JsonRpcBridge
from agent_companion.core.speech_input import AsrResult, AsrRuntimeState


class _ImmediateAsr:
    def transcribe(self, audio: bytes, mime_type: str = "") -> AsrResult:
        if audio != b"RIFFvoice" or mime_type != "audio/wav":
            return AsrResult("", error="asr_failed")
        return AsrResult("你好，Joi", 0.98, "test")


class AsrRpcPipelineTests(unittest.IsolatedAsyncioTestCase):
    def _bridge(self) -> JsonRpcBridge:
        # Construct only the voice boundary. A full bridge starts stores and
        # character services that are irrelevant to the latency contract.
        bridge = object.__new__(JsonRpcBridge)
        bridge.asr = _ImmediateAsr()
        bridge.asr_state = AsrRuntimeState(True, True, "test")
        bridge._voice_generation_lock = threading.Lock()
        bridge._voice_generations = {}
        return bridge

    async def test_rpc_returns_the_transcript_before_the_llm_turn_finishes(self) -> None:
        """Recognition latency must not include planner/LLM response latency."""

        bridge = self._bridge()
        submitted = threading.Event()
        release_llm = threading.Event()

        def slow_submit(text: str) -> dict[str, object]:
            submitted.set()
            release_llm.wait(2)
            return {"ok": True, "events": []}

        bridge.submit_user_text = slow_submit  # type: ignore[method-assign]
        request = {
            "audio_base64": base64.b64encode(b"RIFFvoice").decode("ascii"),
            "mime_type": "audio/wav",
            "thread_id": "thread-voice",
            "generation_id": "voice-1",
        }

        try:
            payload = await asyncio.wait_for(bridge._rpc_voice_transcribe(request), timeout=0.25)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["transcript"], "你好，Joi")
            self.assertTrue(payload["submitted"])
            self.assertEqual(payload["generation_id"], "voice-1")
            self.assertIn("provider_ms", payload["latency"])
            self.assertTrue(await asyncio.to_thread(submitted.wait, 0.5))
        finally:
            release_llm.set()

    async def test_cancelled_generation_is_transcribed_but_never_submitted(self) -> None:
        """Typing a newer intent must retire a late ASR result."""

        bridge = self._bridge()
        entered_provider = threading.Event()
        release_provider = threading.Event()
        submitted: list[str] = []

        class _BlockingAsr:
            def transcribe(self, audio: bytes, mime_type: str = "") -> AsrResult:
                entered_provider.set()
                release_provider.wait(2)
                return AsrResult("旧语音", 1.0, "test")

        bridge.asr = _BlockingAsr()
        bridge.submit_user_text = lambda text: submitted.append(text) or {"ok": True}  # type: ignore[method-assign]
        request = {
            "audio_base64": base64.b64encode(b"RIFFvoice").decode("ascii"),
            "mime_type": "audio/wav",
            "thread_id": "thread-voice",
            "generation_id": "voice-old",
        }

        task = asyncio.create_task(bridge._rpc_voice_transcribe(request))
        self.assertTrue(await asyncio.to_thread(entered_provider.wait, 0.5))
        bridge._rpc_voice_cancel({"thread_id": "thread-voice"})
        release_provider.set()
        payload = await asyncio.wait_for(task, timeout=0.5)

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["submitted"])
        self.assertTrue(payload["stale"])
        self.assertEqual(submitted, [])

    async def test_switching_threads_while_asr_is_running_drops_the_old_transcript(self) -> None:
        bridge = self._bridge()
        entered_provider = threading.Event()
        release_provider = threading.Event()
        submitted: list[str] = []

        class _BlockingAsr:
            def transcribe(self, audio: bytes, mime_type: str = "") -> AsrResult:
                entered_provider.set()
                release_provider.wait(2)
                return AsrResult("旧对话语音", 1.0, "test")

        class _Collaboration:
            active_thread = "thread-old"

            def context(self) -> dict[str, str]:
                return {"thread_id": self.active_thread}

        collaboration = _Collaboration()
        bridge.asr = _BlockingAsr()
        bridge.collaboration = collaboration  # type: ignore[attr-defined]
        bridge.submit_user_text = lambda text: submitted.append(text) or {"ok": True}  # type: ignore[method-assign]
        request = {
            "audio_base64": base64.b64encode(b"RIFFvoice").decode("ascii"),
            "mime_type": "audio/wav",
            "thread_id": "thread-old",
            "generation_id": "voice-old-thread",
        }

        task = asyncio.create_task(bridge._rpc_voice_transcribe(request))
        self.assertTrue(await asyncio.to_thread(entered_provider.wait, 0.5))
        collaboration.active_thread = "thread-new"
        release_provider.set()
        payload = await asyncio.wait_for(task, timeout=0.5)

        self.assertTrue(payload["stale"])
        self.assertFalse(payload["submitted"])
        self.assertEqual(submitted, [])

    async def test_voice_generation_markers_are_globally_bounded(self) -> None:
        bridge = self._bridge()
        for index in range(300):
            bridge._set_voice_generation(f"thread-{index}", f"voice-{index}")

        self.assertLessEqual(len(bridge._voice_generations), 256)
        self.assertTrue(bridge._voice_generation_is_current("thread-299", "voice-299"))


if __name__ == "__main__":
    unittest.main()
