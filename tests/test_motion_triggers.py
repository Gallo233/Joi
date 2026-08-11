"""Ordinary phrasing should reach a motion; talking about one should not.

The alias table only matched a handful of exact forms, so "跳个舞" worked and
"能不能给我跳个舞" did nothing. Widening it to normal speech introduces the
opposite failure -- "聊聊跳舞这个话题" reads as a request to dance -- which is
worse than not recognising the phrase at all, because the character acts on
something the user did not ask for.
"""

from __future__ import annotations

import unittest

from agent_companion.core.character_motion import MOTION_SPECS, character_motion_from_text


class NaturalPhrasingTests(unittest.TestCase):
    def test_everyday_requests_reach_the_motion(self) -> None:
        cases = {
            "跳个舞": "dance",
            "能不能给我跳个舞": "dance",
            "来跳舞吧": "dance",
            "跳支舞看看": "dance",
            "帮我挥挥手": "greet",
            "跟我打个招呼": "greet",
            "say hi": "greet",
            "庆祝一下吧": "happy",
            "开心一下": "happy",
            "比个手枪": "finger_gun",
            "别动了": "idle",
            "安静站着": "idle",
            "stand still": "idle",
            "can you dance for me": "dance",
        }
        for text, motion in cases.items():
            with self.subTest(text=text):
                self.assertEqual(character_motion_from_text(text), motion)

    def test_talking_about_a_motion_does_not_perform_it(self) -> None:
        """Acting on a question is worse than missing it: the user gets a
        movement they never asked for and an answer they did."""

        for text in (
            "我们聊聊跳舞这个话题",
            "舞蹈的历史",
            "跳舞是什么意思",
            "介绍一下华尔兹",
            "解释一下这个动作",
            "talk about dance",
        ):
            with self.subTest(text=text):
                self.assertEqual(character_motion_from_text(text), "")

    def test_unrelated_text_never_triggers(self) -> None:
        for text in ("今天天气不错", "帮我看看这段代码", "", "   "):
            with self.subTest(text=text):
                self.assertEqual(character_motion_from_text(text), "")

    def test_a_more_specific_phrase_wins_over_a_looser_one(self) -> None:
        """"比个手枪" contains "手枪"; ordering decides, so it is pinned."""

        self.assertEqual(character_motion_from_text("比个手枪"), "finger_gun")

    def test_every_motion_is_reachable_by_at_least_one_phrase(self) -> None:
        """A motion nobody can ask for is dead weight in the vocabulary."""

        reachable = {
            character_motion_from_text(text)
            for text in ("跳个舞", "挥挥手", "做个说话动作", "庆祝一下", "比个手枪", "回到待机")
        }
        self.assertEqual(reachable, set(MOTION_SPECS))


if __name__ == "__main__":
    unittest.main()
