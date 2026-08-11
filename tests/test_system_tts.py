"""The OS synthesizer remains testable, but is not a Joi character backend.

Old debug configurations may still contain `tts.fallback_to_system: true`.
The bridge must ignore that value: a provider failure stays silent instead of
letting an operating-system announcer take over the character's identity.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from agent_companion.core import system_tts
from agent_companion.core.tts_bridge import TtsBridge


MACOS_VOICE = sys.platform == "darwin" and system_tts.available()


def write_config(workspace: Path, **tts: object) -> None:
    settings = {"enabled": True, "provider": "system", "fallback_to_system": True, "text_lang": "zh", "speed_factor": 1.0}
    settings.update(tts)
    (workspace / "config.yaml").write_text(yaml.safe_dump({"tts": settings}), encoding="utf-8")


class SystemVoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @unittest.skipUnless(MACOS_VOICE, "system voice is macOS only")
    def test_speaking_produces_audio_a_browser_can_play(self) -> None:
        output = system_tts.synthesize("你好，我是 Joi。", output_dir=self.workspace, language="zh")
        self.assertIsNotNone(output)
        assert output is not None
        # WAVE, because `say` defaults to AIFF and the shell cannot decode it.
        self.assertEqual(output.read_bytes()[:4], b"RIFF")
        self.assertEqual(output.read_bytes()[8:12], b"WAVE")
        self.assertGreater(output.stat().st_size, 1_000)

    @unittest.skipUnless(MACOS_VOICE, "system voice is macOS only")
    def test_the_character_language_picks_the_voice(self) -> None:
        """A Chinese line read by an English voice is worse than silence."""

        installed = {voice.language.casefold() for voice in system_tts.voices()}
        if "zh_cn" not in installed:
            self.skipTest("no Chinese system voice installed")
        self.assertTrue(system_tts.preferred_voice("zh"))
        self.assertTrue(system_tts.preferred_voice("zh_CN"))

    def test_an_unknown_language_falls_back_rather_than_failing(self) -> None:
        self.assertEqual(system_tts.preferred_voice("xx_YY"), "")
        self.assertEqual(system_tts.preferred_voice(""), "")

    def test_empty_text_never_starts_a_subprocess(self) -> None:
        with patch("subprocess.run") as run:
            self.assertIsNone(system_tts.synthesize("   ", output_dir=self.workspace))
            run.assert_not_called()

    def test_speech_rate_maps_onto_the_tools_own_units(self) -> None:
        self.assertEqual(system_tts._rate_to_wpm(1.0), 0, "default rate should not be forced")
        self.assertGreater(system_tts._rate_to_wpm(1.5), 175)
        self.assertLess(system_tts._rate_to_wpm(0.5), 175)
        # Nonsense never reaches the command line.
        for bad in ("fast", None, -3, 99):
            with self.subTest(rate=bad):
                self.assertEqual(system_tts._rate_to_wpm(bad), 0)


class TtsBridgeSystemVoiceProhibitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_an_old_system_provider_configuration_is_ignored(self) -> None:
        write_config(self.workspace)
        bridge = TtsBridge(self.workspace)
        self.assertFalse(bridge.enabled)
        with patch.object(system_tts, "synthesize") as spoken:
            self.assertEqual(bridge.synthesize("测试一句话。"), {})
        spoken.assert_not_called()

    def test_turning_the_flag_off_leaves_no_audio_path(self) -> None:
        write_config(self.workspace, fallback_to_system=False)
        bridge = TtsBridge(self.workspace)
        self.assertFalse(bridge.enabled)
        self.assertEqual(bridge.synthesize("不该出声。"), {})

    def test_tts_disabled_overrides_the_fallback(self) -> None:
        write_config(self.workspace, enabled=False)
        bridge = TtsBridge(self.workspace)
        self.assertFalse(bridge.enabled)
        self.assertEqual(bridge.synthesize("不该出声。"), {})

    def test_a_failing_character_voice_never_falls_back_to_the_system(self) -> None:
        """The selected voice's error is reported, with no substitute audio."""

        write_config(self.workspace, provider="gpt-sovits")
        bridge = TtsBridge(self.workspace)
        with patch.object(TtsBridge, "_synthesize_sovits", return_value={"voice_audio_error": "tts_service_unavailable"}):
            with patch.object(system_tts, "synthesize") as spoken:
                result = bridge.synthesize("服务没起来的时候。")
        self.assertEqual(result["voice_audio_error"], "tts_service_unavailable")
        self.assertNotIn("voice_audio_path", result)
        spoken.assert_not_called()

    def test_status_reports_the_system_voice_separately(self) -> None:
        write_config(self.workspace)
        status = TtsBridge(self.workspace).status_payload()
        self.assertIn("system_fallback", status)
        self.assertIs(status["system_fallback"], False)


if __name__ == "__main__":
    unittest.main()
