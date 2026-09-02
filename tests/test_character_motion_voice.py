"""A motion made the character narrate herself, under someone else's name.

Asking for a dance played the character's own voice saying "来啦。" while the
screen showed "Joi 开始跳舞。" in her speech bubble. Two faults in one string:
it described her in the third person instead of being something she says, and
it used Joi's name rather than the active character's.

The bubble renders `display_card.summary`, so that field is speech, not status.
"""

from __future__ import annotations

import unittest

from agent_companion.core.character_motion import MOTION_SPECS
from agent_companion.core.schemas import ToolRequest
from agent_companion.core.tools.character import CharacterPerformTool


class MotionSpeechTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = CharacterPerformTool()

    def _perform(self, motion: str):
        return self.tool.run(ToolRequest(name="character.perform", arguments={"motion": motion}))

    def test_every_motion_shows_exactly_what_it_says(self) -> None:
        """Heard one line and shown another reads as the app talking over her."""

        for motion in MOTION_SPECS:
            with self.subTest(motion=motion):
                result = self._perform(motion)
                self.assertEqual(result.display_card.summary, result.voice_line.text)
                self.assertTrue(result.voice_line.text.strip(), "a motion the user asked for should say something")

    def test_no_motion_puts_another_characters_name_in_her_mouth(self) -> None:
        """The bubble is attributed to whoever is active, so a hardcoded name
        makes that character introduce herself as a different one."""

        for motion in MOTION_SPECS:
            with self.subTest(motion=motion):
                self.assertNotIn("Joi", self._perform(motion).display_card.summary)

    def test_the_line_is_speech_rather_than_a_description_of_her(self) -> None:
        summary = self._perform("dance").display_card.summary
        self.assertNotIn("开始", summary)
        self.assertNotIn("跳舞", summary, "the bubble is her voice, not a caption of the animation")

    def test_the_mechanical_detail_survives_where_it_belongs(self) -> None:
        """Still worth saying that a motion is local and interruptible -- just
        not in the character's voice."""

        self.assertIn("打断", self._perform("dance").display_card.body)

    def test_the_line_follows_the_users_language_not_the_voices(self) -> None:
        """Which language she *speaks* is a separate setting from which language
        she *writes*. Asking "跳个舞" in Chinese and being answered on screen in
        Japanese, because the voice happened to be Japanese, is the bug."""

        from agent_companion.core.config import _parse_character

        character = _parse_character(
            {
                "name": "测试角色",
                "motion_lines": {"dance": "では、少しだけ。"},
                "motion_lines_by_locale": {"zh": {"dance": "那我跳一下。"}, "ja": {"dance": "では、少しだけ。"}},
            }
        )
        tool = CharacterPerformTool(character)
        for language, expected in (("zh", "那我跳一下。"), ("ja", "では、少しだけ。")):
            with self.subTest(language=language):
                result = tool.run(
                    ToolRequest(name="character.perform", arguments={"motion": "dance", "reply_language": language})
                )
                self.assertEqual(result.display_card.summary, expected)

    def test_a_language_she_has_no_lines_for_never_borrows_the_voices(self) -> None:
        """Falling back to the voice's language is the coupling being broken;
        the shared table is the neutral answer."""

        from agent_companion.core.config import _parse_character

        character = _parse_character(
            {"name": "测试角色", "motion_lines_by_locale": {"ja": {"dance": "では、少しだけ。"}}}
        )
        tool = CharacterPerformTool(character)
        result = tool.run(ToolRequest(name="character.perform", arguments={"motion": "dance", "reply_language": "en"}))
        self.assertEqual(result.display_card.summary, MOTION_SPECS["dance"].voice)

    def _authored(self):
        from agent_companion.core.config import _parse_character

        return _parse_character({"name": "测试角色", "motion_lines": {"dance": "那我跳一下。"}})

    def test_a_character_speaks_her_own_motion_line_when_she_has_one(self) -> None:
        """The shared table is a default mascot's voice: "好耶。" is wrong for a
        character written as quiet and sparing."""

        tool = CharacterPerformTool(self._authored())
        result = tool.run(ToolRequest(name="character.perform", arguments={"motion": "dance"}))
        self.assertEqual(result.display_card.summary, "那我跳一下。")
        self.assertEqual(result.voice_line.text, "那我跳一下。")

    def test_a_motion_she_has_no_line_for_keeps_the_shared_one(self) -> None:
        """Declaring one line must not silence every other motion."""

        tool = CharacterPerformTool(self._authored())
        greet = tool.run(ToolRequest(name="character.perform", arguments={"motion": "greet"}))
        self.assertEqual(greet.display_card.summary, MOTION_SPECS["greet"].voice)

    def test_a_character_without_lines_behaves_exactly_as_before(self) -> None:
        for motion in MOTION_SPECS:
            with self.subTest(motion=motion):
                self.assertEqual(self._perform(motion).display_card.summary, MOTION_SPECS[motion].voice)

    def test_a_motion_that_does_not_exist_still_answers_in_her_voice(self) -> None:
        result = self._perform("moonwalk")
        self.assertFalse(result.ok)
        self.assertEqual(result.display_card.summary, "这个动作还没有准备好。")
        self.assertNotIn("Joi", result.voice_line.text)


if __name__ == "__main__":
    unittest.main()
