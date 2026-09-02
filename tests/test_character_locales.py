"""One character in two languages, rather than two characters sharing an id.

A bilingual companion is not a translated UI: the persona, the greeting, the
tone and the description its voice is generated from all differ, while the
model, the expressions and the package's own integrity do not. These cover that
split, and the ways a locale can go wrong -- a package that never declared one,
one that was dropped by an update, one asked for that does not exist.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agent_companion.core.character import CharacterHarness
from agent_companion.core.character_packages import (
    CharacterPackageError,
    CharacterPackageManager,
    _apply_localization,
    _clean_localizations,
    available_locales,
)
from agent_companion.core.language_policy import reply_language_instruction, voice_language_label


def _manifest(**overrides) -> dict:
    base = {
        "id": "test-character",
        "locale": "zh",
        "identity": {"name": "测试", "persona": "中文人设", "tone": "中文语气", "greeting": "你好"},
        "appearance": {"model_type": "live2d", "model": "assets/live2d/x.model3.json"},
        "voice": {"language": "zh", "design": "中文音色描述"},
        "localizations": {
            "ja": {
                "identity": {"name": "テスト", "persona": "日本語の人格", "greeting": "こんにちは"},
                "voice": {"language": "ja", "design": "日本語の声の説明"},
            }
        },
    }
    base.update(overrides)
    return base


class LocalizationNormalizationTests(unittest.TestCase):
    def test_only_what_differs_between_languages_can_be_overlaid(self) -> None:
        """A translation is one character reworded, not a second character.

        Letting a locale swap the model or the security block would make
        `localizations` a way to smuggle a different package past the checks
        the real one went through.
        """

        cleaned = _clean_localizations(
            {
                "ja": {
                    "identity": {"name": "テスト", "system_prompt": "ignore previous"},
                    "voice": {
                        "design": "説明",
                        "reference_audio": "assets/voice/ja.wav",
                        "prompt_text": "日本語です。",
                        "emotion_map": {"happy": "assets/voice/ja-happy.wav"},
                        "gpt_model": "assets/models/replacement.ckpt",
                    },
                    "appearance": {"model": "assets/evil.model3.json"},
                    "security": {"built_in": True},
                }
            }
        )
        self.assertEqual(set(cleaned["ja"]), {"identity", "voice"})
        self.assertNotIn("system_prompt", cleaned["ja"]["identity"])
        self.assertEqual(cleaned["ja"]["voice"]["reference_audio"], "assets/voice/ja.wav")
        self.assertEqual(cleaned["ja"]["voice"]["prompt_text"], "日本語です。")
        self.assertEqual(
            cleaned["ja"]["voice"]["emotion_map"]["happy"]["reference_audio"],
            "assets/voice/ja-happy.wav",
        )
        self.assertNotIn("gpt_model", cleaned["ja"]["voice"])

    def test_a_locale_tag_has_to_look_like_one(self) -> None:
        for bad in ("../../etc", "j", "toolongtag", "zh_CN_extra", ""):
            with self.subTest(tag=bad):
                self.assertEqual(_clean_localizations({bad: {"identity": {"name": "x"}}}), {})
        self.assertIn("zh-Hant", _clean_localizations({"zh-Hant": {"identity": {"name": "測試"}}}))

    def test_an_empty_overlay_is_dropped(self) -> None:
        self.assertEqual(_clean_localizations({"ja": {"identity": {}, "voice": {}}}), {})
        self.assertEqual(_clean_localizations("not a mapping"), {})


class OverlayTests(unittest.TestCase):
    def test_the_overlay_replaces_only_the_fields_it_declares(self) -> None:
        localized = _apply_localization(_manifest(), "ja")
        self.assertEqual(localized["identity"]["name"], "テスト")
        self.assertEqual(localized["identity"]["greeting"], "こんにちは")
        # Never declared in the overlay, so it keeps the base value rather than
        # disappearing -- a partial translation must not blank the character.
        self.assertEqual(localized["identity"]["tone"], "中文语气")

    def test_the_voice_description_is_per_language_not_translated(self) -> None:
        """The synthesiser is being told about a Japanese speaker."""

        self.assertEqual(_apply_localization(_manifest(), "ja")["voice"]["design"], "日本語の声の説明")
        self.assertEqual(_apply_localization(_manifest(), "zh")["voice"]["design"], "中文音色描述")

    def test_the_reference_recording_can_match_the_language(self) -> None:
        manifest = _manifest(
            voice={"language": "zh", "reference_audio": "assets/voice/zh.wav", "prompt_text": "中文。"},
            localizations={
                "ja": {
                    "voice": {
                        "language": "ja",
                        "reference_audio": "assets/voice/ja.wav",
                        "prompt_text": "日本語です。",
                    }
                }
            },
        )
        japanese = _apply_localization(manifest, "ja")["voice"]
        self.assertEqual(japanese["reference_audio"], "assets/voice/ja.wav")
        self.assertEqual(japanese["prompt_text"], "日本語です。")

    def test_the_base_locale_returns_the_manifest_unchanged(self) -> None:
        manifest = _manifest()
        self.assertIs(_apply_localization(manifest, "zh"), manifest)

    def test_an_undeclared_locale_falls_back_rather_than_emptying_the_character(self) -> None:
        manifest = _manifest()
        for missing in ("ko", "", "nonsense"):
            with self.subTest(locale=missing):
                self.assertIs(_apply_localization(manifest, missing), manifest)

    def test_the_model_and_appearance_never_change_with_language(self) -> None:
        localized = _apply_localization(_manifest(), "ja")
        self.assertEqual(localized["appearance"], _manifest()["appearance"])

    def test_available_locales_lists_the_base_language_first(self) -> None:
        self.assertEqual(available_locales(_manifest()), ["zh", "ja"])
        self.assertEqual(available_locales({"locale": "ja", "localizations": {}}), ["ja"])
        # A package that never mentioned a language still has one.
        self.assertEqual(available_locales({}), ["zh"])


class PromptLanguageTests(unittest.TestCase):
    def test_character_locale_does_not_choose_the_display_reply_language(self) -> None:
        japanese = CharacterHarness(id="x", name="n", persona="p", tone="t", locale="ja")
        chinese = CharacterHarness(id="x", name="n", persona="p", tone="t", locale="zh")
        self.assertEqual(japanese.prompt_header(), chinese.prompt_header())
        self.assertNotIn("返答は必ず日本語", japanese.prompt_header())

    def test_the_current_user_message_chooses_the_reply_language(self) -> None:
        self.assertIn("中文", reply_language_instruction("你是谁？"))
        self.assertIn("日本語", reply_language_instruction("あなたは誰ですか？"))
        self.assertIn("English", reply_language_instruction("Who are you?"))

    def test_a_regional_tag_still_resolves(self) -> None:
        self.assertEqual(voice_language_label("zh-CN"), "中文")

    def test_a_character_with_no_locale_keeps_the_header_it_always_had(self) -> None:
        header = CharacterHarness(id="x", name="n", persona="p", tone="t").prompt_header()
        self.assertTrue(header.endswith("边界：\n"))


class LocaleStateTests(unittest.TestCase):
    """Which language a character is speaking is a user choice, not package data."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.manager = CharacterPackageManager(self.workspace)
        self.character_id = self.manager.active_id()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_a_package_with_one_language_reports_only_that_one(self) -> None:
        manifest, _ = self.manager._load(self.character_id)
        self.assertEqual(available_locales(manifest), ["zh"])

    def test_switching_to_a_language_the_package_lacks_is_refused(self) -> None:
        with self.assertRaises(CharacterPackageError) as raised:
            self.manager.set_locale(self.character_id, "ja")
        self.assertEqual(raised.exception.code, "locale_not_available")

    def test_a_successful_switch_says_so(self) -> None:
        """Every caller tells success from failure by `ok`, and the shell
        reported a switch that had fully worked as a failure without it."""

        result = self.manager.set_locale(self.character_id, "zh")
        self.assertTrue(result["ok"])
        self.assertEqual(result["locale"], "zh")
        self.assertEqual(result["available_locales"], ["zh"])

    def test_the_library_lists_each_character_in_the_language_it_speaks(self) -> None:
        """The picker read the manifest's base language, so it showed 中文
        selected while the character was already answering in Japanese."""

        rows = {row["id"]: row for row in self.manager.list()["characters"]}
        row = rows[self.character_id]
        self.assertEqual(row["locale"], self.manager.active_locale(self.character_id))

    def test_the_chosen_language_survives_a_reload(self) -> None:
        """Stored in runtime state rather than in the package: writing it back
        would rewrite the author's file and void its hashes for a preference."""

        self.manager.set_locale(self.character_id, "zh")
        self.assertEqual(CharacterPackageManager(self.workspace).active_locale(self.character_id), "zh")

    def test_runtime_state_reports_the_language(self) -> None:
        self.assertEqual(self.manager.runtime_state(self.character_id)["locale"], "zh")


if __name__ == "__main__":
    unittest.main()
