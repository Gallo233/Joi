"""MiMo speaks through a chat completion, which changes what can go wrong.

The voice lives in a `user` turn, the line in an `assistant` turn, and the
audio comes back base64 inside the message -- so a 200 with no audio in it is a
real outcome rather than an impossible one. And because `voicedesign` generates
the timbre from that `user` turn, anything appended to it per line risks a
character whose voice drifts between sentences.

These cover the decisions that follow from both.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from agent_companion.core.config import TtsConfig, _parse_character
from agent_companion.core.mimo_tts import VOICE_DESIGN_MODEL, MimoTtsClient
from agent_companion.core.tts_bridge import TtsBridge, _safe_tts_error


DESIGN = "年轻女性，音色清亮干净，语速平稳偏慢。"


class _Config:
    def __init__(self, workspace: Path, **tts: object) -> None:
        self.base_dir = workspace
        settings: dict[str, object] = {"provider": "mimo", "api_key": "sk-secret-value", "model": VOICE_DESIGN_MODEL}
        settings.update(tts)
        self.tts = TtsConfig(**settings)  # type: ignore[arg-type]


def _character(design: str = DESIGN, emotion_map: dict | None = None):
    return _parse_character(
        {
            "name": "测试角色",
            "voice_profiles": [{"id": "default", "label": "默认", "design": design, "emotion_map": emotion_map or {}}],
        }
    )


class _Response:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _StreamingResponse:
    def __init__(self, events: list[dict]) -> None:
        self._lines = [f"data: {json.dumps(event)}\n".encode("utf-8") for event in events]
        self._lines.append(b"data: [DONE]\n")

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self) -> "_StreamingResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _audio_reply(payload: bytes = b"RIFFfake") -> _Response:
    return _Response({"choices": [{"message": {"audio": {"data": base64.b64encode(payload).decode()}}}]})


class RequestShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _sent(self, emotion: str = "neutral", character=None, **tts: object) -> dict:
        config = _Config(self.workspace, **tts)
        client = MimoTtsClient(config)  # type: ignore[arg-type]
        with patch("urllib.request.urlopen", return_value=_audio_reply()) as opened:
            client.synthesize("已经完成了。", character or _character(), self.workspace, emotion)
        self.request = opened.call_args[0][0]
        return json.loads(self.request.data.decode("utf-8"))

    def test_the_voice_goes_in_the_user_turn_and_the_line_in_the_assistant_turn(self) -> None:
        payload = self._sent()
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertIn(DESIGN, payload["messages"][0]["content"])
        self.assertIn("自然、亲近", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1]["role"], "assistant")
        self.assertEqual(payload["messages"][1]["content"], "已经完成了。")

    def test_authentication_uses_the_header_this_api_documents(self) -> None:
        """Not `Authorization: Bearer` -- MiMo reads `api-key`."""

        self._sent()
        self.assertEqual(self.request.get_header("Api-key"), "sk-secret-value")

    def test_the_endpoint_is_a_chat_completion(self) -> None:
        self._sent()
        self.assertEqual(self.request.full_url, "https://api.xiaomimimo.com/v1/chat/completions")

    def test_voice_design_uses_natural_direction_and_no_unsupported_audio_tag(self) -> None:
        """The stable design stays; only its explicitly locked delivery varies."""

        happy = self._sent("happy")
        worried = self._sent("worried")
        self.assertIn(DESIGN, happy["messages"][0]["content"])
        self.assertIn(DESIGN, worried["messages"][0]["content"])
        self.assertNotEqual(happy["messages"][0]["content"], worried["messages"][0]["content"])
        self.assertIn("保持上述同一音色", happy["messages"][0]["content"])
        self.assertEqual(happy["messages"][1]["content"], "已经完成了。")
        self.assertEqual(worried["messages"][1]["content"], "已经完成了。")

    def test_a_neutral_line_carries_no_tag(self) -> None:
        self.assertEqual(self._sent("neutral")["messages"][1]["content"], "已经完成了。")

    def test_every_mood_gets_specific_natural_language_direction(self) -> None:
        for emotion in ("thinking", "serious"):
            with self.subTest(emotion=emotion):
                payload = self._sent(emotion)
                self.assertNotEqual(payload["messages"][0]["content"], self._sent("neutral")["messages"][0]["content"])
                self.assertEqual(payload["messages"][1]["content"], "已经完成了。")

    def test_the_package_can_supply_its_own_natural_direction(self) -> None:
        character = _character(emotion_map={"thinking": {"instructions": "轻声推敲，语义转折处短暂停顿"}})
        payload = self._sent("thinking", character=character)
        self.assertIn("轻声推敲，语义转折处短暂停顿", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1]["content"], "已经完成了。")

    def test_a_short_package_label_supplements_instead_of_erasing_the_director(self) -> None:
        character = _character(emotion_map={"happy": {"instructions": "开心"}})
        payload = self._sent("happy", character=character)
        direction = payload["messages"][0]["content"]
        self.assertIn("克制的欣喜", direction)
        self.assertIn("角色专属补充：开心", direction)
        self.assertIn("语速", direction)
        self.assertIn("重音", direction)

    def test_rewriting_the_line_is_off_unless_asked_for(self) -> None:
        """The same six characters came back 63% longer with it on, so it is
        rewriting rather than tidying -- and Joi decides what is said."""

        self.assertIs(self._sent()["audio"]["optimize_text_preview"], False)
        self.assertIs(self._sent(optimize_text=True)["audio"]["optimize_text_preview"], True)

    def test_a_preset_voice_is_named_instead_of_designed(self) -> None:
        payload = self._sent("happy", model="mimo-v2.5-tts", voice="冰糖")
        self.assertEqual(payload["audio"]["voice"], "冰糖")
        self.assertNotIn("optimize_text_preview", payload["audio"])
        self.assertIn("克制的欣喜", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1]["content"], "已经完成了。")

    def test_structured_delivery_changes_performance_without_changing_the_words(self) -> None:
        config = _Config(self.workspace, model="mimo-v2.5-tts", voice="冰糖")
        client = MimoTtsClient(config)  # type: ignore[arg-type]
        with patch("urllib.request.urlopen", return_value=_audio_reply()) as opened:
            client.synthesize(
                "我在这里。",
                _character(),
                self.workspace,
                "worried",
                {"intensity": 0.3, "pace": "slow", "relation": "supportive"},
            )
        payload = json.loads(opened.call_args[0][0].data.decode("utf-8"))
        self.assertIn("情绪只轻微流露", payload["messages"][0]["content"])
        self.assertIn("语速放慢", payload["messages"][0]["content"])
        self.assertIn("身边支持", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1]["content"], "我在这里。")

    def test_audio_comes_back_as_wav(self) -> None:
        self.assertEqual(self._sent()["audio"]["format"], "wav")


class ResponseHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.client = MimoTtsClient(_Config(self.workspace))  # type: ignore[arg-type]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_the_base64_payload_is_decoded_to_a_file(self) -> None:
        with patch("urllib.request.urlopen", return_value=_audio_reply(b"RIFFreal-bytes")):
            written = self.client.synthesize("你好", _character(), self.workspace)
        self.assertEqual(written.read_bytes(), b"RIFFreal-bytes")
        self.assertEqual(written.suffix, ".wav")

    def test_a_success_with_no_audio_is_an_error_not_a_crash(self) -> None:
        """A refusal, or a model that answered in text, still returns 200."""

        with patch("urllib.request.urlopen", return_value=_Response({"choices": [{"message": {"content": "抱歉"}}]})):
            with self.assertRaises(RuntimeError):
                self.client.synthesize("你好", _character(), self.workspace)

    def test_empty_audio_is_an_error(self) -> None:
        with patch("urllib.request.urlopen", return_value=_audio_reply(b"")):
            with self.assertRaises(RuntimeError):
                self.client.synthesize("你好", _character(), self.workspace)

    def test_the_preset_model_yields_pcm_chunks_without_waiting_for_a_wav(self) -> None:
        client = MimoTtsClient(_Config(self.workspace, model="mimo-v2.5-tts", voice="冰糖"))  # type: ignore[arg-type]
        first = b"\x01\x00\x02\x00"
        second = b"\x03\x00\x04\x00"
        response = _StreamingResponse(
            [
                {"choices": [{"delta": {"audio": None}}]},
                {"choices": [{"delta": {"audio": {"data": base64.b64encode(first).decode()}}}]},
                {"choices": [{"delta": {"audio": {"data": base64.b64encode(second).decode()}}}]},
            ]
        )
        with patch("urllib.request.urlopen", return_value=response) as opened:
            chunks = list(client.stream_pcm16("你好", _character(), "happy"))
        self.assertEqual(chunks, [first, second])
        sent = json.loads(opened.call_args[0][0].data.decode("utf-8"))
        self.assertIs(sent["stream"], True)
        self.assertEqual(sent["audio"]["format"], "pcm16")
        self.assertEqual(sent["audio"]["voice"], "冰糖")

    def test_streaming_is_never_silently_used_for_voice_design(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "preset model"):
            list(self.client.stream_pcm16("你好", _character()))


class BridgeStreamingMetricsTests(unittest.TestCase):
    def test_the_first_real_chunk_records_ttfb_and_receives_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            config = _Config(workspace, enabled=True, model="mimo-v2.5-tts", voice="冰糖")
            config.primary_character = _character()
            seen: dict[str, object] = {}

            class Client:
                def stream_pcm16(self, text, character, emotion, delivery):
                    seen.update({"text": text, "emotion": emotion, "delivery": delivery})
                    yield b"\x01\x00\x02\x00"

            bridge = TtsBridge(workspace)
            bridge._config = config  # type: ignore[assignment]
            bridge._client = Client()
            with patch("time.perf_counter", side_effect=[10.0, 10.25, 10.8]):
                rows = list(bridge.synthesize_stream("你好", "happy", {"intensity": 0.7}))
            self.assertEqual(seen["delivery"], {"intensity": 0.7})
            self.assertEqual(rows[0]["voice_audio_ttfb_ms"], 250)
            self.assertEqual(rows[-1]["voice_audio_total_ms"], 800)
            self.assertEqual(bridge.status_payload()["last_ttfb_ms"], 250)


class RefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _refuses(self, character=None, **tts: object) -> str:
        client = MimoTtsClient(_Config(self.workspace, **tts))  # type: ignore[arg-type]
        with patch("urllib.request.urlopen") as opened:
            with self.assertRaises(RuntimeError) as raised:
                client.synthesize("你好", character or _character(), self.workspace)
        opened.assert_not_called()
        return str(raised.exception)

    def test_no_key_never_reaches_the_network(self) -> None:
        self.assertEqual(_safe_tts_error(RuntimeError(self._refuses(api_key=""))), "tts_config_error")

    def test_voicedesign_without_a_description_is_refused_rather_than_guessed(self) -> None:
        """There is no default voice for this model: an empty description
        would hand the character whatever the service felt like."""

        message = self._refuses(character=_character(design=""))
        self.assertEqual(_safe_tts_error(RuntimeError(message)), "tts_config_error")

    def test_an_auth_failure_becomes_a_code_without_the_key(self) -> None:
        failure = urllib.error.HTTPError("https://api.xiaomimimo.com/v1/chat/completions", 401, "no", {}, None)  # type: ignore[arg-type]
        self.assertEqual(_safe_tts_error(failure), "tts_auth_failed")
        self.assertNotIn("sk-secret", _safe_tts_error(failure))


if __name__ == "__main__":
    unittest.main()
