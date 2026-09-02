"""The middle rung between `say` and GPT-SoVITS.

The system voice cannot be steered and GPT-SoVITS is an afternoon of install,
which left everything the voice path does *around* the audio unverified against
a real signal. A hosted endpoint needs only a key and takes direction, so these
cover the parts that decide whether it can stand in: what gets sent, what
happens to a provider that refuses the direction, and that a failure never
carries the key back to the settings panel.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import yaml

from agent_companion.core.cloud_tts import CloudTtsClient
from agent_companion.core.config import TtsConfig, _parse_character
from agent_companion.core.tts_bridge import TtsBridge, _safe_tts_error


class _Config:
    def __init__(self, workspace: Path, **tts: object) -> None:
        self.base_dir = workspace
        settings: dict[str, object] = {
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-secret-value",
            "model": "tts-1",
            "voice": "alloy",
            "speed_factor": 1.0,
        }
        settings.update(tts)
        self.tts = TtsConfig(**settings)  # type: ignore[arg-type]
        self.characters = [_parse_character({"name": "Joi", "voice_profiles": [{"id": "default", "label": "默认"}]})]

    @property
    def primary_character(self):
        return self.characters[0]


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class RequestShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _speak(self, emotion: str = "neutral", **tts: object) -> dict:
        config = _Config(self.workspace, **tts)
        client = CloudTtsClient(config)  # type: ignore[arg-type]
        with patch("urllib.request.urlopen", return_value=_Response(b"RIFFfake")) as opened:
            client.synthesize("已经完成了。", config.primary_character, self.workspace, emotion)
        return json.loads(opened.call_args[0][0].data.decode("utf-8"))

    def test_the_line_is_sent_as_a_speech_request(self) -> None:
        payload = self._speak()
        self.assertEqual(payload["input"], "已经完成了。")
        self.assertEqual(payload["model"], "tts-1")
        self.assertEqual(payload["voice"], "alloy")

    def test_audio_comes_back_as_wav_rather_than_the_api_default(self) -> None:
        """The shell has to decode it and the lip sync analyser has to read it."""

        self.assertEqual(self._speak()["response_format"], "wav")

    def test_the_mood_is_sent_as_direction(self) -> None:
        """This is the point of the cloud rung: emotion the voice can act on."""

        self.assertTrue(self._speak("happy")["instructions"])
        self.assertNotEqual(self._speak("happy")["instructions"], self._speak("worried")["instructions"])

    def test_a_neutral_line_is_given_no_direction(self) -> None:
        self.assertNotIn("instructions", self._speak("neutral"))

    def test_the_endpoint_is_the_openai_compatible_one(self) -> None:
        config = _Config(self.workspace)
        client = CloudTtsClient(config)  # type: ignore[arg-type]
        with patch("urllib.request.urlopen", return_value=_Response(b"RIFFfake")) as opened:
            client.synthesize("你好", config.primary_character, self.workspace)
        self.assertEqual(opened.call_args[0][0].full_url, "https://api.example.com/v1/audio/speech")

    def test_a_trailing_slash_does_not_double_up(self) -> None:
        config = _Config(self.workspace, base_url="https://api.example.com/v1/")
        client = CloudTtsClient(config)  # type: ignore[arg-type]
        with patch("urllib.request.urlopen", return_value=_Response(b"RIFFfake")) as opened:
            client.synthesize("你好", config.primary_character, self.workspace)
        self.assertEqual(opened.call_args[0][0].full_url, "https://api.example.com/v1/audio/speech")


class DirectionFallbackTests(unittest.TestCase):
    """Only some models accept `instructions`; the rest answer 400 on it."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.config = _Config(self.workspace)
        self.client = CloudTtsClient(self.config)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _refuse_then_accept(self):
        calls: list[dict] = []

        def urlopen(request, timeout=None):
            payload = json.loads(request.data.decode("utf-8"))
            calls.append(payload)
            if "instructions" in payload:
                raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, None)  # type: ignore[arg-type]
            return _Response(b"RIFFfake")

        return calls, urlopen

    def test_a_refused_direction_still_produces_speech(self) -> None:
        calls, urlopen = self._refuse_then_accept()
        with patch("urllib.request.urlopen", side_effect=urlopen):
            self.client.synthesize("你好", self.config.primary_character, self.workspace, "happy")
        self.assertEqual(len(calls), 2)
        self.assertNotIn("instructions", calls[-1])

    def test_the_refusal_is_remembered_rather_than_rediscovered(self) -> None:
        """One wasted request per process, not per line."""

        calls, urlopen = self._refuse_then_accept()
        with patch("urllib.request.urlopen", side_effect=urlopen):
            self.client.synthesize("你好", self.config.primary_character, self.workspace, "happy")
            calls.clear()
            self.client.synthesize("再见", self.config.primary_character, self.workspace, "happy")
        self.assertEqual(len(calls), 1)

    def test_an_error_that_is_not_about_the_direction_is_not_retried(self) -> None:
        """Retrying a 500 or a 401 without direction just fails twice."""

        attempts = []

        def urlopen(request, timeout=None):
            attempts.append(request)
            raise urllib.error.HTTPError(request.full_url, 500, "Server Error", {}, None)  # type: ignore[arg-type]

        with patch("urllib.request.urlopen", side_effect=urlopen):
            with self.assertRaises(urllib.error.HTTPError):
                self.client.synthesize("你好", self.config.primary_character, self.workspace, "happy")
        self.assertEqual(len(attempts), 1)


class UnconfiguredTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _refuses(self, **tts: object) -> str:
        config = _Config(self.workspace, **tts)
        client = CloudTtsClient(config)  # type: ignore[arg-type]
        with patch("urllib.request.urlopen") as opened:
            with self.assertRaises(RuntimeError) as raised:
                client.synthesize("你好", config.primary_character, self.workspace)
        opened.assert_not_called()
        return str(raised.exception)

    def test_a_missing_key_never_reaches_the_network(self) -> None:
        self.assertEqual(_safe_tts_error(RuntimeError(self._refuses(api_key=""))), "tts_config_error")

    def test_a_missing_base_url_never_reaches_the_network(self) -> None:
        self.assertEqual(_safe_tts_error(RuntimeError(self._refuses(base_url=""))), "tts_config_error")

    def test_a_missing_model_never_reaches_the_network(self) -> None:
        self.assertEqual(_safe_tts_error(RuntimeError(self._refuses(model=""))), "tts_config_error")

    def test_an_unexpanded_placeholder_is_not_a_key(self) -> None:
        config = TtsConfig(base_url="https://api.example.com/v1", model="tts-1", api_key="${TTS_KEY}")
        self.assertFalse(config.is_cloud_configured)


class ErrorSanitisationTests(unittest.TestCase):
    def test_an_http_status_becomes_a_code_the_panel_can_show(self) -> None:
        for status, expected in ((401, "tts_auth_failed"), (403, "tts_auth_failed"), (429, "tts_rate_limited")):
            with self.subTest(status=status):
                failure = urllib.error.HTTPError("https://api.example.com/v1/audio/speech", status, "no", {}, None)  # type: ignore[arg-type]
                self.assertEqual(_safe_tts_error(failure), expected)

    def test_a_failure_never_carries_the_key_back(self) -> None:
        """`last_error` is rendered in the settings panel."""

        failure = urllib.error.HTTPError("https://api.example.com/v1/audio/speech?key=sk-secret", 401, "no", {}, None)  # type: ignore[arg-type]
        self.assertNotIn("sk-secret", _safe_tts_error(failure))


class SettingsSurfaceTests(unittest.TestCase):
    """The panel could switch the voice provider but never finish setting it up.

    `tts.model`, `tts.voice` and `tts.base_url` had neither a writable spec nor
    a place in the settings payload, so choosing a hosted provider left the
    fields it needs unreachable -- and the panel showed a provider it could not
    make work.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        (self.workspace / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "tts": {
                        "enabled": True,
                        "provider": "mimo",
                        "model": "mimo-v2.5-tts-voicedesign",
                        "base_url": "https://api.example.com/v1",
                        "api_key": "sk-secret-value",
                        "timeout_seconds": 120,
                    },
                    "llm": {"provider": "openai_compatible", "model": "deepseek-v4-flash"},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _settings(self) -> dict:
        from agent_companion.core.config import load_app_config
        from agent_companion.core.server import _safe_runtime_settings

        return _safe_runtime_settings(load_app_config(self.workspace / "config.yaml"))

    def test_the_panel_can_see_how_the_voice_is_configured(self) -> None:
        tts = self._settings()["tts"]
        self.assertEqual(tts["provider"], "mimo")
        self.assertEqual(tts["model"], "mimo-v2.5-tts-voicedesign")
        self.assertEqual(tts["base_url"], "https://api.example.com/v1")

    def test_the_panel_can_see_which_text_model_is_in_use(self) -> None:
        llm = self._settings()["llm"]
        self.assertEqual(llm["model"], "deepseek-v4-flash")
        self.assertEqual(llm["provider"], "openai_compatible")

    def test_the_key_is_never_part_of_what_the_panel_sees(self) -> None:
        self.assertNotIn("sk-secret-value", json.dumps(self._settings(), ensure_ascii=False))
        self.assertNotIn("api_key", self._settings()["tts"])

    def test_the_new_voice_fields_are_writable(self) -> None:
        from agent_companion.core.runtime_config_writer import _ALLOWED_FIELDS

        for field in ("model", "voice", "base_url", "audio_format", "optimize_text", "timeout_seconds", "gpt_sovits_streaming_mode"):
            with self.subTest(field=field):
                self.assertIn(("tts", field), _ALLOWED_FIELDS)

    def test_the_key_stays_unwritable_through_the_panel(self) -> None:
        from agent_companion.core.runtime_config_writer import _ALLOWED_FIELDS, _is_sensitive_path

        self.assertNotIn(("tts", "api_key"), _ALLOWED_FIELDS)
        self.assertTrue(_is_sensitive_path(("tts", "api_key")))


class BridgeRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, **tts: object) -> None:
        settings: dict[str, object] = {
            "enabled": True,
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-secret-value",
            "model": "tts-1",
            "fallback_to_system": True,
            "text_lang": "zh",
        }
        settings.update(tts)
        (self.workspace / "config.yaml").write_text(yaml.safe_dump({"tts": settings}), encoding="utf-8")

    def test_the_cloud_voice_is_labelled_so_the_panel_can_say_which_spoke(self) -> None:
        self._write()
        bridge = TtsBridge(self.workspace)
        self.assertTrue(bridge.enabled)
        with patch("urllib.request.urlopen", return_value=_Response(b"RIFFfake")):
            result = bridge.synthesize("你好。", emotion="happy")
        self.assertEqual(result.get("voice_audio_source"), "cloud")
        self.assertTrue(Path(result["voice_audio_path"]).is_file())

    def test_a_cloud_failure_reports_error_without_substitute_audio(self) -> None:
        self._write()
        bridge = TtsBridge(self.workspace)
        failure = urllib.error.HTTPError("https://api.example.com/v1/audio/speech", 401, "no", {}, None)  # type: ignore[arg-type]
        with patch("urllib.request.urlopen", side_effect=failure):
            result = bridge.synthesize("你好。")
        self.assertEqual(result["voice_audio_error"], "tts_auth_failed")
        self.assertNotIn("voice_audio_path", result)
        self.assertNotIn("voice_audio_source", result)

    def test_an_unconfigured_cloud_provider_does_not_claim_to_be_ready(self) -> None:
        self._write(api_key="")
        status = TtsBridge(self.workspace).status_payload()
        self.assertEqual(status["provider"], "openai_compatible")
        self.assertNotIn("sk-secret", json.dumps(status))

    def test_mimo_status_exposes_the_actual_latency_mode_without_a_system_fallback(self) -> None:
        self._write(provider="mimo", model="mimo-v2.5-tts", voice="冰糖", timeout_seconds=30)
        status = TtsBridge(self.workspace).status_payload()
        self.assertTrue(status["streaming"])
        self.assertEqual(status["model"], "mimo-v2.5-tts")
        self.assertEqual(status["timeout_seconds"], 30)
        self.assertIs(status["system_fallback"], False)


if __name__ == "__main__":
    unittest.main()
