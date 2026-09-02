"""The character's own voice could never start on anything but Windows.

`_ensure_server_started` looked for `runtime\\python.exe` and nothing else, so a
Mac with GPT-SoVITS fully installed and working still fell through to the
system voice -- and reported it as a service that was merely unavailable, which
sends the user to look at the wrong thing.

These cover launching on this platform, and refusing to launch in the ways that
leave a usable message behind.
"""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
import wave
from unittest.mock import patch

from agent_companion.core.config import TtsConfig
from agent_companion.core.gpt_sovits import GptSoVitsClient
from agent_companion.core.tts_bridge import _safe_tts_error


class _Config:
    """Only the parts of AppConfig the launcher reads."""

    def __init__(self, base_dir: Path, **tts: object) -> None:
        self.base_dir = base_dir
        settings: dict[str, object] = {"server_url": "http://127.0.0.1:9880/"}
        settings.update(tts)
        self.tts = TtsConfig(**settings)  # type: ignore[arg-type]


class _Character:
    """Only the parts of CharacterConfig the synthesis payload reads."""

    sprites: list[object] = []

    def __init__(self, text_lang: str) -> None:
        self._text_lang = text_lang

    def voice_text_lang(self, fallback: str) -> str:
        return self._text_lang or fallback

    def voice_prompt_lang(self, fallback: str) -> str:
        return self._text_lang or fallback

    def voice_refer_audio_path(self) -> str:
        return ""

    def voice_prompt_text(self) -> str:
        return ""

    def voice_speech_speed(self, fallback: float) -> float:
        return fallback

    def voice_emotion(self, _emotion: str) -> object | None:
        return None


class SpokenLanguageTests(unittest.TestCase):
    """A Japanese voice handed a Chinese line read it as kanji readings.

    That is not an accent: nothing in the sentence survives. The voice itself --
    weights and reference clip -- is chosen by the user and never changes here;
    only the phonetics follow the words actually written.
    """

    def _payload(self, voice_lang: str, text: str) -> dict[str, object]:
        client = GptSoVitsClient(_Config(Path("/tmp"), text_lang=voice_lang, prompt_lang=voice_lang))  # type: ignore[arg-type]
        with patch.object(GptSoVitsClient, "_ensure_server_started"), patch.object(GptSoVitsClient, "_switch_model"):
            return client._synthesis_payload(text, _Character(voice_lang), "1", "neutral")  # type: ignore[arg-type]

    def test_a_chinese_line_under_a_japanese_voice_is_pronounced_as_chinese(self) -> None:
        self.assertEqual(self._payload("ja", "我们先看看今天的安排。")["text_lang"], "zh")

    def test_a_line_in_the_selected_language_keeps_that_language(self) -> None:
        self.assertEqual(self._payload("ja", "今日の予定を確認します。")["text_lang"], "ja")
        self.assertEqual(self._payload("zh", "我们先看看今天的安排。")["text_lang"], "zh")

    def test_the_reference_voice_is_never_swapped_by_the_language_check(self) -> None:
        payload = self._payload("ja", "我们先看看今天的安排。")
        self.assertEqual(payload["prompt_lang"], "ja")


class InterpreterDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _install(self, *interpreter: str) -> Path:
        base = self.root / "GPT-SoVITS"
        base.mkdir(parents=True, exist_ok=True)
        (base / "api_v2.py").write_text("", encoding="utf-8")
        if interpreter:
            python = base.joinpath(*interpreter)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
        return base

    def _client(self, base: Path, **tts: object) -> GptSoVitsClient:
        return GptSoVitsClient(_Config(self.root, gpt_sovits_work_path=str(base), **tts))  # type: ignore[arg-type]

    def test_a_unix_virtualenv_is_found_the_way_the_windows_bundle_is(self) -> None:
        for parts in ((".venv", "bin", "python"), ("venv", "bin", "python"), ("runtime", "bin", "python")):
            with self.subTest(interpreter="/".join(parts)):
                with tempfile.TemporaryDirectory() as scratch:
                    self.root = Path(scratch)
                    base = self._install(*parts)
                    self.assertEqual(self._client(base)._interpreter(base), base.joinpath(*parts))

    def test_the_windows_bundle_still_wins_where_it_exists(self) -> None:
        base = self._install("runtime", "python.exe")
        self.assertEqual(self._client(base)._interpreter(base), base / "runtime" / "python.exe")

    def test_a_declared_interpreter_beats_the_search(self) -> None:
        base = self._install(".venv", "bin", "python")
        chosen = self.root / "conda" / "envs" / "sovits" / "bin" / "python"
        chosen.parent.mkdir(parents=True, exist_ok=True)
        chosen.write_text("", encoding="utf-8")
        self.assertEqual(self._client(base, gpt_sovits_python=str(chosen))._interpreter(base), chosen)

    def test_a_declared_interpreter_that_does_not_exist_is_not_quietly_replaced(self) -> None:
        """Falling back to the search would run a different Python than asked for."""

        base = self._install(".venv", "bin", "python")
        client = self._client(base, gpt_sovits_python=str(self.root / "missing" / "python"))
        self.assertIsNone(client._interpreter(base))

    def test_an_install_with_no_interpreter_is_never_run_with_jois_own(self) -> None:
        """Joi has no torch, so its interpreter would fail in a confusing way."""

        base = self._install()
        self.assertIsNone(self._client(base)._interpreter(base))


class LaunchRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _start(self, **tts: object) -> str:
        client = GptSoVitsClient(_Config(self.root, **tts))  # type: ignore[arg-type]
        with patch.object(GptSoVitsClient, "_server_alive", return_value=False):
            with patch("subprocess.Popen") as popen:
                with self.assertRaises(RuntimeError) as raised:
                    client._ensure_server_started()
        popen.assert_not_called()
        return str(raised.exception)

    def test_no_configured_path_reads_as_not_installed(self) -> None:
        self.assertEqual(_safe_tts_error(RuntimeError(self._start())), "tts_not_installed")

    def test_a_path_with_no_api_script_reads_as_not_installed(self) -> None:
        self.assertEqual(_safe_tts_error(RuntimeError(self._start(gpt_sovits_work_path=str(self.root)))), "tts_not_installed")

    def test_an_install_with_no_interpreter_reads_as_not_installed(self) -> None:
        base = self.root / "GPT-SoVITS"
        base.mkdir()
        (base / "api_v2.py").write_text("", encoding="utf-8")
        message = self._start(gpt_sovits_work_path=str(base))
        self.assertEqual(_safe_tts_error(RuntimeError(message)), "tts_not_installed")
        # The message has to name the way out, because the settings panel only
        # shows the code and the log is where the user looks next.
        self.assertIn("gpt_sovits_python", message)

    def test_a_running_server_needs_no_install_at_all(self) -> None:
        """The ordinary Mac setup: the user starts api_v2.py themselves."""

        client = GptSoVitsClient(_Config(self.root))  # type: ignore[arg-type]
        with patch.object(GptSoVitsClient, "_server_alive", return_value=True):
            with patch("subprocess.Popen") as popen:
                client._ensure_server_started()
        popen.assert_not_called()


class UnhealthyServiceTests(unittest.TestCase):
    """A service can answer its port for hours and still synthesize nothing.

    That is how this failed in practice: `/control` kept returning 200 while
    every `/tts` returned 500, so `_server_alive` read healthy, nothing
    restarted it, and with the system voice off the character went silent
    overnight with only a generic "合成失败" to show for it.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base = self.root / "GPT-SoVITS"
        (self.base / "runtime" / "bin").mkdir(parents=True)
        (self.base / "api_v2.py").write_text("", encoding="utf-8")
        (self.base / "runtime" / "bin" / "python").write_text("", encoding="utf-8")
        self.config = _Config(self.root, gpt_sovits_work_path=str(self.base))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _http_error(self, status: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError("http://127.0.0.1:9880/tts", status, "no", {}, BytesIO(b"Internal Server Error"))  # type: ignore[arg-type]

    def test_a_five_hundred_restarts_the_service_and_retries_once(self) -> None:
        client = GptSoVitsClient(self.config)  # type: ignore[arg-type]
        client._server_process = object()  # Joi owns this one, so it may restart it.
        answers = [self._http_error(500), b"RIFFaudio"]

        def urlopen(request, timeout=None):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer

            class _Ok:
                def read(self_inner) -> bytes:
                    return answer

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *_: object) -> None:
                    return None

            return _Ok()

        with patch.object(GptSoVitsClient, "shutdown"):
            with patch.object(GptSoVitsClient, "_ensure_server_started"):
                with patch("urllib.request.urlopen", side_effect=urlopen):
                    data = client._post_bytes("tts", {"text": "x"}, timeout=5)
        self.assertEqual(data, b"RIFFaudio")
        self.assertEqual(answers, [], "the retry should have consumed the second answer")

    def test_a_service_joi_did_not_start_is_reported_rather_than_killed(self) -> None:
        """Someone else's process is theirs to manage; the message has to say
        what to do instead of a silent, futile retry."""

        client = GptSoVitsClient(self.config)  # type: ignore[arg-type]
        self.assertIsNone(client._server_process)
        with patch("urllib.request.urlopen", side_effect=self._http_error(500)):
            with self.assertRaises(RuntimeError) as raised:
                client._post_bytes("tts", {"text": "x"}, timeout=5)
        message = str(raised.exception)
        self.assertIn("restart", message.casefold())
        self.assertEqual(_safe_tts_error(RuntimeError(message)), "tts_service_unhealthy")

    def test_a_broken_service_is_not_reported_as_merely_unavailable(self) -> None:
        """`unavailable` sends the user to check whether it is running -- and
        it is running, which is the whole trap."""

        self.assertEqual(_safe_tts_error(RuntimeError("GPT-SoVITS HTTP 500: Internal Server Error")), "tts_service_unhealthy")
        self.assertNotEqual(_safe_tts_error(RuntimeError("GPT-SoVITS HTTP 500")), "tts_service_unavailable")

    def test_a_client_error_is_not_retried(self) -> None:
        """A 400 is the request being wrong; restarting fixes nothing."""

        client = GptSoVitsClient(self.config)  # type: ignore[arg-type]
        client._server_process = object()
        attempts = []

        def urlopen(request, timeout=None):
            attempts.append(request)
            raise self._http_error(400)

        with patch.object(GptSoVitsClient, "_ensure_server_started") as restarted:
            with patch("urllib.request.urlopen", side_effect=urlopen):
                with self.assertRaises(RuntimeError):
                    client._post_bytes("tts", {"text": "x"}, timeout=5)
        self.assertEqual(len(attempts), 1)
        restarted.assert_not_called()


class ServiceLifetimeTests(unittest.TestCase):
    """The service lives as long as Joi does, and no longer.

    Weights occupy several gigabytes, so leaving them resident after the app
    closes is a real cost. The awkward case is a force-quit: the shutdown path
    never runs, the service is left behind, and the next launch finds it alive
    but not its own -- unable to stop it and unable to restart it when it
    breaks, which is exactly the state that produced a silent character.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base = self.root / "GPT-SoVITS"
        (self.base / "runtime" / "bin").mkdir(parents=True)
        (self.base / "api_v2.py").write_text("", encoding="utf-8")
        (self.base / "runtime" / "bin" / "python").write_text("", encoding="utf-8")
        self.config = _Config(self.root, gpt_sovits_work_path=str(self.base))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _client(self) -> GptSoVitsClient:
        return GptSoVitsClient(self.config)  # type: ignore[arg-type]

    def test_starting_the_service_records_which_process_is_ours(self) -> None:
        client = self._client()
        with patch.object(GptSoVitsClient, "_server_alive", return_value=False):
            with patch("subprocess.Popen") as popen:
                popen.return_value.pid = 4242
                popen.return_value.poll.return_value = None
                with patch.object(GptSoVitsClient, "_wait_for_server"):
                    client._ensure_server_started()
        self.assertEqual(client._pid_file().read_text(encoding="utf-8").strip(), "4242")

    def test_a_service_left_behind_by_a_force_quit_is_reclaimed(self) -> None:
        client = self._client()
        client._remember_started_pid(4242)
        with patch.object(GptSoVitsClient, "_server_alive", return_value=True):
            with patch("agent_companion.core.gpt_sovits._process_is_gpt_sovits", return_value=True):
                client._ensure_server_started()
        self.assertEqual(client._adopted_pid, 4242, "an orphan Joi started must become Joi's again")

    def test_a_recycled_pid_is_never_adopted(self) -> None:
        """The number outlives the process, and terminating whatever inherited
        it would be far worse than leaving a service running."""

        client = self._client()
        client._remember_started_pid(4242)
        with patch.object(GptSoVitsClient, "_server_alive", return_value=True):
            with patch("agent_companion.core.gpt_sovits._process_is_gpt_sovits", return_value=False):
                client._ensure_server_started()
        self.assertEqual(client._adopted_pid, 0)

    def test_a_service_the_user_started_is_left_running(self) -> None:
        """Starting one on launch does not make every instance Joi's to kill."""

        client = self._client()
        with patch.object(GptSoVitsClient, "_server_alive", return_value=True):
            client._ensure_server_started()
        with patch("os.kill") as killed:
            client.stop_owned_server()
        killed.assert_not_called()

    def test_quitting_stops_the_service_joi_started(self) -> None:
        client = self._client()
        client._remember_started_pid(4242)
        with patch.object(GptSoVitsClient, "_server_alive", return_value=True):
            with patch("agent_companion.core.gpt_sovits._process_is_gpt_sovits", return_value=True):
                client._ensure_server_started()
                with patch("os.kill") as killed:
                    client.stop_owned_server()
        killed.assert_called_once()
        self.assertFalse(client._pid_file().exists(), "a stopped service must not be adopted next launch")

    def test_reloading_a_character_does_not_tear_down_the_service(self) -> None:
        """Only quitting should stop it; a reload happens mid-conversation."""

        from agent_companion.core.tts_bridge import TtsBridge

        bridge = TtsBridge.__new__(TtsBridge)
        stopped: list[str] = []

        class _Client:
            def shutdown(self) -> None:
                stopped.append("shutdown")

            def stop_owned_server(self) -> None:
                stopped.append("stop_owned_server")

        bridge._client = _Client()
        bridge.shutdown()
        self.assertEqual(stopped, ["shutdown"])

        bridge._client = _Client()
        bridge.stop_local_service()
        self.assertEqual(stopped, ["shutdown", "stop_owned_server"])


class StartupWaitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base = self.root / "GPT-SoVITS"
        (self.base / "runtime" / "bin").mkdir(parents=True)
        (self.base / "api_v2.py").write_text("", encoding="utf-8")
        (self.base / "runtime" / "bin" / "python").write_text("", encoding="utf-8")
        self.config = _Config(self.root, gpt_sovits_work_path=str(self.base))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_synthesis_waits_for_the_models_to_load(self) -> None:
        """Weights take tens of seconds; the first request used to race them."""

        client = GptSoVitsClient(self.config)  # type: ignore[arg-type]
        answers = iter([False, False, False, True])
        with patch.object(GptSoVitsClient, "_server_alive", side_effect=lambda: next(answers)):
            with patch("subprocess.Popen") as popen:
                popen.return_value.poll.return_value = None
                with patch("time.sleep"):
                    client._ensure_server_started()
        popen.assert_called_once()

    def test_a_launch_that_dies_fails_immediately_instead_of_waiting_out_the_clock(self) -> None:
        """A crashed server never answers, and waiting only delays the fallback."""

        client = GptSoVitsClient(self.config)  # type: ignore[arg-type]
        with patch.object(GptSoVitsClient, "_server_alive", return_value=False):
            with patch("subprocess.Popen") as popen:
                popen.return_value.poll.return_value = 1
                with patch("time.sleep") as slept:
                    with self.assertRaises(RuntimeError) as raised:
                        client._ensure_server_started()
        slept.assert_not_called()
        self.assertEqual(_safe_tts_error(RuntimeError(str(raised.exception))), "tts_not_installed")

    def test_a_server_that_never_comes_up_reports_a_timeout(self) -> None:
        client = GptSoVitsClient(self.config)  # type: ignore[arg-type]
        with patch.object(GptSoVitsClient, "_server_alive", return_value=False):
            with patch("subprocess.Popen") as popen:
                popen.return_value.poll.return_value = None
                with patch("time.sleep"):
                    # Time only moves when the clock is asked, so the deadline
                    # is crossed without the test actually waiting for it.
                    with patch("time.monotonic", side_effect=[0.0, 1.0, 10_000.0]):
                        with self.assertRaises(RuntimeError) as raised:
                            client._ensure_server_started()
        self.assertEqual(_safe_tts_error(RuntimeError(str(raised.exception))), "tts_timeout")


class VoiceWarmupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _character(self, *, prompt_text: str = "きっと大丈夫。"):
        from agent_companion.core.config import _parse_character

        return _parse_character(
            {
                "name": "テスト",
                "voice_profiles": [
                    {
                        "id": "ja",
                        "label": "日本語",
                        "text_lang": "ja",
                        "prompt_lang": "ja",
                        "refer_audio_path": "/voice/reference.wav",
                        "prompt_text": prompt_text,
                    }
                ],
            }
        )

    def test_background_warmup_primes_japanese_inference_without_playing_it(self) -> None:
        client = GptSoVitsClient(_Config(self.root, text_lang="ja", prompt_lang="ja", gpt_sovits_streaming_mode=3))  # type: ignore[arg-type]
        with patch.object(client, "_ensure_server_started"):
            with patch.object(client, "_switch_model"):
                with patch.object(client, "_post_bytes", return_value=b"RIFF") as posted:
                    client.warmup(self._character())
        payload = posted.call_args[0][1]
        self.assertEqual(payload["text"], "準備できました。")
        self.assertEqual(payload["text_lang"], "ja")
        self.assertEqual(payload["streaming_mode"], 3)

    def test_a_character_without_a_complete_reference_still_warms_the_server(self) -> None:
        client = GptSoVitsClient(_Config(self.root))  # type: ignore[arg-type]
        with patch.object(client, "_ensure_server_started") as started:
            with patch.object(client, "_switch_model") as switched:
                with patch.object(client, "_post_bytes") as posted:
                    client.warmup(self._character(prompt_text=""))
        started.assert_called_once()
        switched.assert_called_once()
        posted.assert_not_called()


class StreamingProtocolTests(unittest.TestCase):
    class _Response:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload
            self.offset = 0

        def read(self, size: int = -1) -> bytes:
            if size < 0:
                size = len(self.payload) - self.offset
            data = self.payload[self.offset : self.offset + size]
            self.offset += len(data)
            return data

        def read1(self, size: int = -1) -> bytes:
            return self.read(min(size, 3) if size >= 0 else 3)

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _wav_header(sample_rate: int = 32000) -> bytes:
        output = BytesIO()
        with wave.open(output, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            writer.writeframes(b"")
        return output.getvalue()

    def test_local_stream_parses_rate_and_preserves_split_pcm_samples(self) -> None:
        client = GptSoVitsClient(_Config(self.root, gpt_sovits_streaming_mode=2))  # type: ignore[arg-type]
        character = object()
        pcm = b"\x01\x00\x02\x00\x03\x00"
        response = self._Response(self._wav_header() + pcm)
        with patch.object(client, "_synthesis_payload", return_value={"text": "你好"}):
            with patch("urllib.request.urlopen", return_value=response) as opened:
                chunks = list(client.stream_pcm16("你好", character))  # type: ignore[arg-type]
        self.assertEqual(b"".join(chunk for chunk, _ in chunks), pcm)
        self.assertEqual({rate for _, rate in chunks}, {32000})
        sent = json.loads(opened.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent["streaming_mode"], 2)
        self.assertEqual(sent["media_type"], "wav")

    def test_invalid_stream_header_fails_instead_of_playing_noise(self) -> None:
        client = GptSoVitsClient(_Config(self.root))  # type: ignore[arg-type]
        with patch.object(client, "_synthesis_payload", return_value={"text": "你好"}):
            with patch("urllib.request.urlopen", return_value=self._Response(b"not-a-wave")):
                with self.assertRaisesRegex(RuntimeError, "invalid WAV"):
                    list(client.stream_pcm16("你好", object()))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
