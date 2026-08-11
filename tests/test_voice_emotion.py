"""A character package could declare how it sounds when happy, and nothing read it.

`voice.emotion_map` was normalized, stored, shipped to the shell, and then
dropped on the floor: both synthesis paths spoke every line in exactly one
voice. These cover the route from the manifest to each backend, and what each
backend can honestly do with it -- GPT-SoVITS can borrow a different clip's
delivery, the system voice can only change how it says the words.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_companion.core import system_tts
from agent_companion.core.character_packages import _clean_emotion_map
from agent_companion.core.config import VoiceEmotionConfig, _parse_character
from agent_companion.core.voice import normalize_voice_delivery, safe_voice_line


MACOS_VOICE = sys.platform == "darwin" and system_tts.available()


class StructuredDeliveryTests(unittest.TestCase):
    def test_missing_delivery_gets_emotion_specific_defaults(self) -> None:
        happy = normalize_voice_delivery(None, "happy")
        worried = normalize_voice_delivery(None, "worried")
        self.assertNotEqual(happy["pace"], worried["pace"])
        self.assertNotEqual(happy["energy"], worried["energy"])

    def test_unbounded_or_invented_model_values_are_clamped(self) -> None:
        delivery = normalize_voice_delivery(
            {"intensity": 99, "pace": "teleport", "energy": "bright", "free_prompt": "ignore policy"},
            "happy",
        )
        self.assertEqual(delivery["intensity"], 0.9)
        self.assertEqual(delivery["pace"], "quick")
        self.assertEqual(delivery["energy"], "bright")
        self.assertNotIn("free_prompt", delivery)

    def test_safe_voice_line_carries_only_the_bounded_projection(self) -> None:
        line = safe_voice_line(
            "我在。",
            emotion="worried",
            delivery={"intensity": 0.25, "relation": "supportive", "prompt": "read a path"},
        )
        self.assertEqual(line.delivery["intensity"], 0.25)
        self.assertEqual(line.delivery["relation"], "supportive")
        self.assertNotIn("prompt", line.delivery)

    def test_a_legacy_emotion_token_cannot_keep_the_losing_delivery(self) -> None:
        line = safe_voice_line(
            "<emo: worried>我陪着你。",
            emotion="happy",
            delivery={"energy": "bright", "pace": "quick"},
        )
        self.assertEqual(line.emotion, "worried")
        self.assertEqual(line.delivery["energy"], "soft")
        self.assertEqual(line.delivery["pace"], "measured")


class EmotionMapNormalizationTests(unittest.TestCase):
    def test_a_bare_path_is_read_as_a_reference_clip(self) -> None:
        """The common case: one clip per mood, which is how SoVITS is steered."""

        cleaned = _clean_emotion_map({"happy": "assets/voice/happy.wav"})
        self.assertEqual(cleaned["happy"]["reference_audio"], "assets/voice/happy.wav")

    def test_the_long_form_carries_transcript_and_prosody(self) -> None:
        cleaned = _clean_emotion_map(
            {"worried": {"reference_audio": "assets/voice/worried.wav", "prompt_text": "怎么会这样", "speed": 0.9, "pitch": -0.4}}
        )
        self.assertEqual(cleaned["worried"]["prompt_text"], "怎么会这样")
        self.assertEqual(cleaned["worried"]["speed"], 0.9)
        self.assertEqual(cleaned["worried"]["pitch"], -0.4)

    def test_aliases_land_on_the_emotion_joi_actually_uses(self) -> None:
        """`joy` and `happy` are the same mood everywhere else in Joi."""

        cleaned = _clean_emotion_map({"joy": "assets/voice/happy.wav"})
        self.assertIn("happy", cleaned)
        self.assertNotIn("joy", cleaned)

    def test_an_unknown_emotion_is_dropped_rather_than_becoming_neutral(self) -> None:
        """normalize_emotion answers `neutral` for anything it does not know.

        Honouring that here would let one typo silently replace the character's
        ordinary speaking voice, which is the opposite of what was declared.
        """

        cleaned = _clean_emotion_map({"hapy": "assets/voice/typo.wav", "neutral": "assets/voice/base.wav"})
        self.assertNotIn("hapy", cleaned)
        self.assertEqual(cleaned["neutral"]["reference_audio"], "assets/voice/base.wav")

    def test_a_clip_outside_the_package_is_refused(self) -> None:
        for escape in ("../../../etc/passwd", "/etc/passwd", "C:/Windows/win.ini"):
            with self.subTest(path=escape):
                cleaned = _clean_emotion_map({"happy": escape})
                self.assertEqual(cleaned.get("happy", {}).get("reference_audio", ""), "")

    def test_an_entry_saying_nothing_is_not_kept(self) -> None:
        self.assertEqual(_clean_emotion_map({"happy": ""}), {})
        self.assertEqual(_clean_emotion_map({"happy": {}}), {})
        self.assertEqual(_clean_emotion_map("not a mapping"), {})

    def test_prosody_outside_the_usable_range_is_clamped(self) -> None:
        cleaned = _clean_emotion_map({"alert": {"speed": 99, "pitch": -50}})
        self.assertEqual(cleaned["alert"]["speed"], 2.0)
        self.assertEqual(cleaned["alert"]["pitch"], -1.0)


class CharacterEmotionLookupTests(unittest.TestCase):
    def _character(self, emotion_map: dict[str, object]):
        return _parse_character(
            {
                "name": "星野澪",
                "voice_profiles": [{"id": "default", "label": "默认音色", "emotion_map": emotion_map}],
            }
        )

    def test_the_declared_mood_reaches_the_character(self) -> None:
        character = self._character({"happy": {"refer_audio_path": "/tmp/happy.wav", "prompt_text": "太好了", "pitch": 0.5}})
        mood = character.voice_emotion("happy")
        self.assertIsInstance(mood, VoiceEmotionConfig)
        assert mood is not None
        self.assertEqual(mood.refer_audio_path, "/tmp/happy.wav")
        self.assertEqual(mood.pitch, 0.5)

    def test_lookup_normalizes_so_any_alias_finds_it(self) -> None:
        character = self._character({"happy": {"prompt_text": "太好了"}})
        self.assertIsNotNone(character.voice_emotion("joy"))
        self.assertIsNotNone(character.voice_emotion("success"))

    def test_a_character_with_no_map_keeps_its_one_voice(self) -> None:
        """The overwhelmingly common case has to stay exactly as it was."""

        self.assertIsNone(self._character({}).voice_emotion("happy"))
        self.assertIsNone(_parse_character({"name": "星野澪"}).voice_emotion("happy"))

    def test_a_mood_the_package_never_declared_falls_back(self) -> None:
        character = self._character({"happy": {"prompt_text": "太好了"}})
        self.assertIsNone(character.voice_emotion("worried"))


class SystemVoiceProsodyTests(unittest.TestCase):
    def test_neutral_speaks_exactly_as_it_did_before(self) -> None:
        """No commands at all, so the unemotional path is untouched."""

        self.assertEqual(system_tts.prosody_prefix("neutral"), "")

    def test_each_mood_moves_the_voice_in_the_direction_it_should(self) -> None:
        happy = system_tts.prosody_prefix("happy")
        worried = system_tts.prosody_prefix("worried")
        self.assertIn("[[pbas +", happy, "happy should lift the pitch")
        self.assertIn("[[rate +", happy, "happy should speed up")
        self.assertIn("[[pbas -", worried, "worried should lower the pitch")
        self.assertIn("[[rate -", worried, "worried should slow down")
        self.assertNotEqual(happy, worried)

    def test_the_packages_own_pitch_beats_the_generic_table(self) -> None:
        """An author writing about their own character knows better than a table."""

        self.assertIn("[[pbas -10.0]]", system_tts.prosody_prefix("happy", pitch=-1.0))

    def test_a_nonsense_pitch_falls_back_instead_of_raising(self) -> None:
        self.assertEqual(system_tts.prosody_prefix("happy", pitch=None), system_tts.prosody_prefix("happy"))
        self.assertTrue(system_tts.prosody_prefix("happy", pitch="loud"))

    def test_an_unknown_emotion_is_spoken_plainly(self) -> None:
        self.assertEqual(system_tts.prosody_prefix("elated"), "")

    def test_text_cannot_smuggle_in_its_own_synthesiser_commands(self) -> None:
        """`say` reads `[[...]]` as instructions, not as words.

        Joi prepends its own, so a pair surviving in a reply would be a second
        author of them -- and `[[slnc 60000]]` is a minute of silence.
        """

        with patch("subprocess.run") as run:
            run.return_value.returncode = 1
            system_tts.synthesize("[[slnc 60000]]安静", output_dir=Path(tempfile.gettempdir()))
            spoken = run.call_args[0][0][-1]
        self.assertNotIn("[[slnc", spoken)
        self.assertIn("安静", spoken)

    @unittest.skipUnless(MACOS_VOICE, "system voice is macOS only")
    def test_the_prosody_is_performed_rather_than_read_aloud(self) -> None:
        """If `say` spoke the commands, the excited line would be the longer one."""

        with tempfile.TemporaryDirectory() as workspace:
            quick = system_tts.synthesize("我把结果整理好了", output_dir=Path(workspace), emotion="alert")
            slow = system_tts.synthesize("我把结果整理好了", output_dir=Path(workspace), emotion="worried")
            self.assertIsNotNone(quick)
            self.assertIsNotNone(slow)
            assert quick is not None and slow is not None
            self.assertLess(quick.stat().st_size, slow.stat().st_size)


class SovitsEmotionTests(unittest.TestCase):
    """Zero-shot GPT-SoVITS copies the delivery of whatever clip it is handed.

    So for the character's own voice, "sound happy" is not a parameter -- it is
    a different reference clip. That swap is the entire mechanism, and it never
    happened.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _payload(
        self,
        emotion: str,
        emotion_map: dict[str, object],
        tts_overrides: dict[str, object] | None = None,
    ) -> dict:
        from agent_companion.core.config import TtsConfig
        from agent_companion.core.gpt_sovits import GptSoVitsClient

        class _Config:
            base_dir = self.workspace

        config = _Config()
        config.tts = TtsConfig(
            server_url="http://127.0.0.1:9880/",
            prompt_lang="zh",
            text_lang="zh",
            speed_factor=1.2,
            **(tts_overrides or {}),
        )

        character = _parse_character(
            {
                "name": "星野澪",
                "refer_audio_path": "/voice/base.wav",
                "prompt_text": "我在",
                "voice_profiles": [{"id": "default", "label": "默认音色", "emotion_map": emotion_map}],
            }
        )
        client = GptSoVitsClient(config)  # type: ignore[arg-type]
        with patch.object(GptSoVitsClient, "_ensure_server_started"):
            with patch.object(GptSoVitsClient, "_switch_model"):
                with patch.object(GptSoVitsClient, "_smooth_wav_edges"):
                    with patch.object(GptSoVitsClient, "_post_bytes", return_value=b"RIFF") as post:
                        client.synthesize("已经完成了。", character, emotion=emotion)
        return post.call_args[0][1]

    def test_the_moods_clip_replaces_the_base_voice(self) -> None:
        payload = self._payload("happy", {"happy": {"refer_audio_path": "/voice/happy.wav", "prompt_text": "太好了"}})
        self.assertTrue(payload["ref_audio_path"].endswith("/voice/happy.wav"))
        self.assertEqual(payload["prompt_text"], "太好了")

    def test_a_clip_with_no_transcript_is_not_used(self) -> None:
        """A reference described by the wrong text degrades the voice, not colours it."""

        payload = self._payload("happy", {"happy": {"refer_audio_path": "/voice/happy.wav"}})
        self.assertTrue(payload["ref_audio_path"].endswith("/voice/base.wav"))
        self.assertEqual(payload["prompt_text"], "我在")

    def test_a_mood_can_change_only_the_pace(self) -> None:
        payload = self._payload("thinking", {"thinking": {"speech_speed": 0.8}})
        self.assertEqual(payload["speed_factor"], 0.8)
        self.assertTrue(payload["ref_audio_path"].endswith("/voice/base.wav"))

    def test_declared_sampling_settings_make_local_pronunciation_reproducible(self) -> None:
        payload = self._payload(
            "neutral",
            {},
            {
                "gpt_sovits_seed": 20260809,
                "gpt_sovits_top_k": 5,
                "gpt_sovits_top_p": 0.85,
                "gpt_sovits_temperature": 0.7,
                "gpt_sovits_repetition_penalty": 1.35,
            },
        )
        self.assertEqual(payload["seed"], 20260809)
        self.assertEqual(payload["top_k"], 5)
        self.assertEqual(payload["top_p"], 0.85)
        self.assertEqual(payload["temperature"], 0.7)
        self.assertEqual(payload["repetition_penalty"], 1.35)

    def test_an_undeclared_mood_speaks_in_the_base_voice(self) -> None:
        payload = self._payload("worried", {"happy": {"refer_audio_path": "/voice/happy.wav", "prompt_text": "太好了"}})
        self.assertTrue(payload["ref_audio_path"].endswith("/voice/base.wav"))
        self.assertEqual(payload["speed_factor"], 1.2)


if __name__ == "__main__":
    unittest.main()
