from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from agent_companion.core.character import CharacterHarness
from agent_companion.core.codex_runtime import CodexRuntimeSession
from agent_companion.core.language_policy import (
    display_language_policy,
    obvious_language_mismatch,
    obvious_voice_language_mismatch,
)
from agent_companion.core.model_call import CallOutcome
from agent_companion.core.schemas import ToolRequest
from agent_companion.core.tools.chat import CompanionChatTool


class _Character:
    name = "AvatarSample_A"
    setting = "日本語で書かれたキャラクター設定"
    sprites: list[object] = []

    def __init__(self, voice_language: str) -> None:
        self.voice_language = voice_language

    def voice_text_lang(self, fallback: str) -> str:
        return self.voice_language or fallback


def _config(voice_language: str):
    config = type("Config", (), {})()
    config.llm = type("Llm", (), {"is_configured": True, "use_mock": False, "temperature": 0.5})()
    config.tts = type("Tts", (), {"text_lang": "zh"})()
    config.primary_character = _Character(voice_language)
    config.characters = [config.primary_character]
    return config


class LanguagePolicyTests(unittest.TestCase):
    def test_unambiguous_scripts_follow_the_current_message(self) -> None:
        self.assertEqual(display_language_policy("你是谁？").code, "zh")
        self.assertEqual(display_language_policy("あなたは誰ですか？").code, "ja")
        self.assertEqual(display_language_policy("누구세요?").code, "ko")
        self.assertEqual(display_language_policy("Who are you?").code, "en")
        self.assertEqual(display_language_policy("¿Quién eres?").code, "latn")

    def test_a_chinese_message_rejects_a_kana_reply(self) -> None:
        self.assertTrue(obvious_language_mismatch("你是谁？", "僕はアバターです。"))
        self.assertFalse(obvious_language_mismatch("你是谁？", "我是你的桌面伙伴。"))

    def test_the_voice_has_its_own_language_check(self) -> None:
        self.assertTrue(obvious_voice_language_mismatch("zh-CN", "こんにちは。"))
        self.assertFalse(obvious_voice_language_mismatch("ja-JP", "こんにちは。"))

    def test_codex_display_prompt_is_not_overridden_by_a_japanese_character(self) -> None:
        character = CharacterHarness("x", "テスト", "日本語の人格", "自然", locale="ja")
        app = SimpleNamespace(
            character=character,
            memory=SimpleNamespace(context=lambda *_args, **_kwargs: []),
            background_context=SimpleNamespace(status=lambda: {}),
            _memory_scope=lambda: {},
        )
        runtime = object.__new__(CodexRuntimeSession)
        runtime.app = app
        prompt = runtime._build_prompt("你是谁？", include_harness=True)
        self.assertIn("最终显示回复语言（最高优先级）", prompt)
        self.assertIn("本轮提示：中文", prompt)
        self.assertNotIn("返答は必ず日本語", prompt)

    def test_codex_final_text_is_repaired_when_the_prompt_is_still_ignored(self) -> None:
        runtime = object.__new__(CodexRuntimeSession)
        runtime.workspace = Path("/tmp")
        config = _config("ja")
        with (
            patch("agent_companion.core.codex_runtime.load_workspace_config", return_value=config),
            patch(
                "agent_companion.core.codex_runtime.chat_completion",
                return_value=CallOutcome(True, '{"reply":"我是桌面上的伙伴。"}'),
            ),
        ):
            reply, repaired, language = runtime._ensure_display_language("你是谁？", "僕はデスクトップの相棒です。")
        self.assertEqual(reply, "我是桌面上的伙伴。")
        self.assertTrue(repaired)
        self.assertEqual(language, "zh")


class ChatLanguageChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _tool(self, voice_language: str) -> CompanionChatTool:
        tool = CompanionChatTool(self.workspace)
        tool._config = _config(voice_language)
        return tool

    def test_chinese_display_and_japanese_voice_are_generated_in_one_call(self) -> None:
        calls: list[dict] = []

        def complete(_config, route, messages, **kwargs):
            calls.append({"route": route, "messages": messages, "kwargs": kwargs})
            return CallOutcome(
                True,
                '{"reply":"我是 AvatarSample_A，会在桌面陪着你。","voice_text":"僕はAvatarSample_A。デスクトップで一緒にいる相棒です。","emotion":"neutral"}',
            )

        tool = self._tool("ja")
        with patch("agent_companion.core.tools.chat.chat_completion", complete):
            result = tool.run(ToolRequest("companion.chat", {"text": "你是谁？"}))

        self.assertEqual(len(calls), 1)
        self.assertIn("我是", result.display_card.summary)
        self.assertIn("僕は", result.voice_line.text)
        self.assertEqual(result.agent_state["display_language"], "zh")
        self.assertEqual(result.agent_state["voice_language"], "ja")
        prompt = calls[0]["messages"][0]["content"]
        self.assertIn("reply 跟随用户输入语言", prompt)
        self.assertIn("voice_text 才使用 日本語", prompt)

    def test_an_obvious_channel_mixup_is_repaired_without_changing_the_voice_choice(self) -> None:
        outputs = [
            '{"reply":"僕はAvatarSample_Aです。","voice_text":"僕はAvatarSample_Aです。","emotion":"neutral"}',
            '{"reply":"我是 AvatarSample_A。","voice_text":"僕はAvatarSample_Aです。"}',
        ]

        def complete(_config, _route, messages, **_kwargs):
            del messages
            return CallOutcome(True, outputs.pop(0))

        tool = self._tool("ja")
        with patch("agent_companion.core.tools.chat.chat_completion", complete):
            result = tool.run(ToolRequest("companion.chat", {"text": "你是谁？"}))

        self.assertEqual(result.display_card.summary, "我是 AvatarSample_A。")
        self.assertEqual(result.voice_line.text, "僕はAvatarSample_Aです。")
        self.assertTrue(result.agent_state["display_language_repaired"])
        self.assertEqual(outputs, [])

    def test_a_japanese_message_can_keep_japanese_text_while_chinese_voice_is_selected(self) -> None:
        outputs = [
            '{"reply":"ここにいますよ。","voice_text":"ここにいますよ。","emotion":"happy"}',
            '{"reply":"ここにいますよ。","voice_text":"我在这里。"}',
        ]

        def complete(_config, _route, messages, **_kwargs):
            del messages
            return CallOutcome(True, outputs.pop(0))

        tool = self._tool("zh")
        with patch("agent_companion.core.tools.chat.chat_completion", complete):
            result = tool.run(ToolRequest("companion.chat", {"text": "どこにいますか？"}))

        self.assertEqual(result.display_card.summary, "ここにいますよ。")
        self.assertEqual(result.voice_line.text, "我在这里。")
        self.assertTrue(result.agent_state["display_language_repaired"])


if __name__ == "__main__":
    unittest.main()
