"""Voice input through MiMo, which shapes recognition as a chat completion.

The audio rides inside a user turn rather than being uploaded as a file, so
this cannot reuse the OpenAI-compatible transcription client. Two of its limits
are load-bearing and neither is obvious: it reads only WAV and MP3, and its
language parameter accepts only `zh`, `en` and `auto` -- anything else is a
400, which would have made a Japanese-speaking character unable to listen at
all rather than merely listen badly.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import yaml

from agent_companion.core.config import AsrConfig
from agent_companion.core.speech_input import (
    MimoAsrProvider,
    _mimo_language,
    build_asr_provider,
)


def _config(**overrides) -> AsrConfig:
    settings = {
        "enabled": True,
        "provider": "mimo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.5-asr",
        "api_key": "sk-secret-value",
        "language": "zh",
        "timeout_seconds": 60,
    }
    settings.update(overrides)
    return AsrConfig(**settings)  # type: ignore[arg-type]


class _Response:
    def __init__(self, transcript: str) -> None:
        self._body = json.dumps({"choices": [{"message": {"content": transcript}}]}).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class RequestShapeTests(unittest.TestCase):
    def _sent(self, audio: bytes = b"RIFFfake", mime: str = "audio/wav", **overrides) -> dict:
        provider = MimoAsrProvider(_config(**overrides))
        with patch("urllib.request.urlopen", return_value=_Response("你好")) as opened:
            provider.transcribe(audio, mime)
        self.request = opened.call_args[0][0]
        return json.loads(self.request.data.decode("utf-8"))

    def test_the_audio_rides_inside_a_user_turn(self) -> None:
        payload = self._sent()
        part = payload["messages"][0]["content"][0]
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertEqual(part["type"], "input_audio")
        self.assertTrue(part["input_audio"]["data"].startswith("data:audio/wav;base64,"))

    def test_the_audio_is_sent_intact(self) -> None:
        payload = self._sent(audio=b"RIFF-real-bytes")
        encoded = payload["messages"][0]["content"][0]["input_audio"]["data"].split(",", 1)[1]
        self.assertEqual(base64.b64decode(encoded), b"RIFF-real-bytes")

    def test_authentication_uses_the_header_this_api_documents(self) -> None:
        self._sent()
        self.assertEqual(self.request.get_header("Api-key"), "sk-secret-value")

    def test_the_endpoint_is_a_chat_completion(self) -> None:
        self._sent()
        self.assertEqual(self.request.full_url, "https://api.xiaomimimo.com/v1/chat/completions")

    def test_mp3_is_labelled_as_mp3(self) -> None:
        payload = self._sent(mime="audio/mpeg")
        self.assertTrue(payload["messages"][0]["content"][0]["input_audio"]["data"].startswith("data:audio/mpeg;base64,"))


class LanguageTests(unittest.TestCase):
    """`zh`, `en`, `auto` -- anything else is a 400 from the service."""

    def test_the_two_supported_languages_pass_through(self) -> None:
        self.assertEqual(_mimo_language("zh"), "zh")
        self.assertEqual(_mimo_language("en"), "en")
        self.assertEqual(_mimo_language("zh-CN"), "zh")
        self.assertEqual(_mimo_language("en_US"), "en")

    def test_an_unsupported_language_becomes_auto_rather_than_a_rejected_request(self) -> None:
        """A Japanese-speaking character would otherwise fail every request.

        `auto` transcribes Japanese only approximately, but listening badly
        beats not listening at all, and the alternative is a 400.
        """

        for language in ("ja", "ja-JP", "ko", "", "nonsense"):
            with self.subTest(language=language):
                self.assertEqual(_mimo_language(language), "auto")


class AudioGuardTests(unittest.TestCase):
    def _refused(self, audio: bytes, mime: str) -> str:
        provider = MimoAsrProvider(_config())
        with patch("urllib.request.urlopen") as opened:
            result = provider.transcribe(audio, mime)
        opened.assert_not_called()
        return result.error

    def test_a_container_the_service_cannot_read_is_refused_before_upload(self) -> None:
        """MediaRecorder's own formats. Uploading them wastes the round trip
        and returns an error about the wrong thing."""

        for mime in ("audio/webm", "audio/mp4", "audio/ogg", ""):
            with self.subTest(mime=mime):
                self.assertEqual(self._refused(b"data", mime), "audio_format_unsupported")

    def test_empty_audio_never_reaches_the_network(self) -> None:
        self.assertEqual(self._refused(b"", "audio/wav"), "empty_audio")

    def test_audio_beyond_the_documented_ceiling_is_refused_locally(self) -> None:
        """10 MB is measured after base64, which is a third larger again."""

        self.assertEqual(self._refused(b"x" * (9 * 1024 * 1024), "audio/wav"), "audio_too_large")


class ResponseTests(unittest.TestCase):
    def _result(self, response):
        provider = MimoAsrProvider(_config())
        with patch("urllib.request.urlopen", return_value=response):
            return provider.transcribe(b"RIFFfake", "audio/wav")

    def test_the_transcript_comes_back(self) -> None:
        result = self._result(_Response("你来啦，今天要做什么？"))
        self.assertTrue(result.ok)
        self.assertEqual(result.transcript, "你来啦，今天要做什么？")
        self.assertEqual(result.provider, "mimo")

    def test_silence_is_reported_rather_than_returned_as_an_empty_success(self) -> None:
        self.assertEqual(self._result(_Response("   ")).error, "empty_transcript")

    def test_a_success_carrying_no_transcript_is_an_error_not_a_crash(self) -> None:
        class _Empty:
            def read(self) -> bytes:
                return b'{"choices": []}'

            def __enter__(self):
                return self

            def __exit__(self, *_: object) -> None:
                return None

        self.assertEqual(self._result(_Empty()).error, "empty_transcript")


class FailureTests(unittest.TestCase):
    def _error(self, status: int) -> str:
        provider = MimoAsrProvider(_config())
        failure = urllib.error.HTTPError("https://api.xiaomimimo.com/v1/chat/completions", status, "no", {}, None)  # type: ignore[arg-type]
        with patch("urllib.request.urlopen", side_effect=failure):
            return provider.transcribe(b"RIFFfake", "audio/wav").error

    def test_an_empty_account_says_so_rather_than_looking_like_a_bug(self) -> None:
        """Topping up is the only fix; retrying never helps."""

        self.assertEqual(self._error(402), "asr_insufficient_balance")

    def test_auth_and_rate_limits_get_their_own_codes(self) -> None:
        self.assertEqual(self._error(401), "asr_auth_failed")
        self.assertEqual(self._error(403), "asr_auth_failed")
        self.assertEqual(self._error(429), "asr_rate_limited")

    def test_a_failure_never_carries_the_key_back(self) -> None:
        provider = MimoAsrProvider(_config())
        failure = urllib.error.HTTPError("https://api.xiaomimimo.com/v1?key=sk-secret-value", 500, "no", {}, None)  # type: ignore[arg-type]
        with patch("urllib.request.urlopen", side_effect=failure):
            result = provider.transcribe(b"RIFFfake", "audio/wav")
        self.assertNotIn("sk-secret", json.dumps(result.__dict__, ensure_ascii=False))


class ProviderSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, **asr: object) -> None:
        settings = {
            "enabled": True,
            "provider": "mimo",
            "base_url": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.5-asr",
            "api_key": "sk-secret-value",
            "language": "zh",
        }
        settings.update(asr)
        (self.workspace / "config.yaml").write_text(yaml.safe_dump({"asr": settings}), encoding="utf-8")

    def test_the_mimo_provider_is_chosen_for_this_provider_name(self) -> None:
        self._write()
        provider, state = build_asr_provider(self.workspace)
        self.assertIsInstance(provider, MimoAsrProvider)
        self.assertTrue(state.configured)
        self.assertEqual(state.provider, "mimo")

    def test_an_unconfigured_mimo_provider_does_not_claim_to_be_ready(self) -> None:
        self._write(api_key="")
        provider, state = build_asr_provider(self.workspace)
        self.assertNotIsInstance(provider, MimoAsrProvider)
        self.assertFalse(state.configured)


if __name__ == "__main__":
    unittest.main()
