"""The language a user picks, and the one that is not theirs to pick here.

Joi has three languages and they used to be one decision made three times: the
shell was Chinese, the reply took whatever language the message was written in,
and the character package's language quietly moved both the voice and the
persona. This covers the settings that separate them -- interface and chat
belong to the user, and the spoken language stays with the character package.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import yaml

from agent_companion.core.config import load_app_config
from agent_companion.core.language_policy import CHAT_LANGUAGE_CHOICES
from agent_companion.core.runtime_config_writer import preview_runtime_config_update


class LanguageConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, language: dict[str, object] | None = None) -> Path:
        payload: dict[str, object] = {
            "characters": [{"name": "测试角色", "setting": "测试"}],
        }
        if language is not None:
            payload["language"] = language
        path = self.workspace / "config.yaml"
        path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
        return path

    def test_chat_defaults_to_chinese_and_the_interface_is_chinese_only(self) -> None:
        config = load_app_config(self._write())
        self.assertEqual(config.language.chat, "zh")
        self.assertEqual(config.language.interface, "zh")
        self.assertFalse(config.language.chat_follows_user)

    def test_follow_is_kept_and_a_value_joi_cannot_write_in_is_not(self) -> None:
        self.assertTrue(load_app_config(self._write({"chat": "follow"})).language.chat_follows_user)
        self.assertTrue(load_app_config(self._write({"chat": "auto"})).language.chat_follows_user)
        self.assertEqual(load_app_config(self._write({"chat": "ja-JP"})).language.chat, "ja")
        self.assertEqual(load_app_config(self._write({"chat": "klingon"})).language.chat, "zh")

    def test_an_untranslated_interface_language_is_not_stored_as_if_it_existed(self) -> None:
        self.assertEqual(load_app_config(self._write({"interface": "en"})).language.interface, "zh")

    def test_the_panel_can_write_the_chat_language(self) -> None:
        self._write()
        for choice in CHAT_LANGUAGE_CHOICES:
            with self.subTest(choice=choice):
                result = preview_runtime_config_update(self.workspace, {"language": {"chat": choice}})
                self.assertTrue(result.ok, result.errors)

    def test_the_panel_cannot_invent_a_language_or_translate_the_shell(self) -> None:
        self._write()
        rejected = preview_runtime_config_update(self.workspace, {"language": {"chat": "klingon"}})
        self.assertFalse(rejected.ok)
        untranslated = preview_runtime_config_update(self.workspace, {"language": {"interface": "en"}})
        self.assertFalse(untranslated.ok)

    def test_the_spoken_language_is_not_settable_from_the_language_panel(self) -> None:
        # It belongs to the character package, which is where the voice is chosen.
        from agent_companion.core.runtime_config_writer import _ALLOWED_FIELDS

        self.assertNotIn(("language", "voice"), _ALLOWED_FIELDS)

    def test_the_shell_is_told_all_three_languages_in_one_place(self) -> None:
        from agent_companion.core.server import JsonRpcBridge

        self._write({"chat": "zh"})
        payload = JsonRpcBridge(self.workspace)._language_payload()
        self.assertEqual(payload["interface"], "zh")
        self.assertEqual(payload["chat"], "zh")
        self.assertEqual(payload["interface_choices"], ["zh"])
        self.assertIn("follow", payload["chat_choices"])
        # Reported so the panel can name it, never written from there.
        self.assertIn("voice", payload)


if __name__ == "__main__":
    unittest.main()
