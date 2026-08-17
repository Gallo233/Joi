from __future__ import annotations

import json
import unittest

from agent_companion.core.minecraft_planner import compile_plan, compile_single_action


def _compiler(raw: str) -> str:
    return raw


class MinecraftPlannerTests(unittest.TestCase):
    def test_valid_multi_step_plan_is_canonicalized_and_estimated(self) -> None:
        plan = compile_plan(
            "采 10 根原木然后造一把镐",
            lambda _prompt: _compiler(
                '{"summary":"采集并合成","steps":['
                '{"action":"collect","block":"oak_log","count":10,"radius":16,"dimension":"overworld"},'
                '{"action":"craft","item":"wooden_pickaxe","count":1}]}'
            ),
        )
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(len(plan["steps"]), 2)
        self.assertEqual(plan["estimated_actions"], 2)
        self.assertEqual(plan["estimated_changes"], 10)
        self.assertEqual(plan["steps"][0]["action"], "collect")

    def test_attack_is_forbidden_in_plans(self) -> None:
        plan = compile_plan(
            "打怪",
            lambda _prompt: _compiler('{"summary":"打","steps":[{"action":"attack","count":1}]}'),
        )
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["error"], "plan_attack_forbidden")

    def test_oversized_empty_or_invalid_plans_fail_closed(self) -> None:
        oversized = json.dumps({"summary": "x", "steps": [{"action": "observe"}] * 9})
        for raw, code in (
            ('{"summary":"x","steps":[]}', "plan_steps_invalid"),
            (oversized, "plan_steps_invalid"),
            ('{"summary":"x","steps":[{"action":"explode"}]}', "plan_step_invalid"),
            ('{"summary":"x","steps":[{"action":"observe","javascript":"x"}]}', "plan_step_invalid"),
            ("not json", "plan_compile_failed"),
            ("{}", "plan_steps_invalid"),
        ):
            with self.subTest(code=code):
                plan = compile_plan("目标", lambda _prompt, raw=raw: raw)
                self.assertFalse(plan["ok"])
                self.assertEqual(plan["error"], code)

    def test_single_action_compiles_or_returns_none(self) -> None:
        intent = compile_single_action("帮我挖三块橡木", lambda _prompt: _compiler('{"intent":{"action":"mine","block":"oak_log","count":3}}'))
        self.assertIsNotNone(intent)
        self.assertEqual(intent["action"], "mine")
        self.assertEqual(intent["count"], 3)
        for raw in ('{"intent":null}', "garbage", '{"intent":{"action":"attack"}}', '{"intent":{"action":"explode"}}', ""):
            with self.subTest(raw=raw):
                self.assertIsNone(compile_single_action("指令", lambda _prompt, raw=raw: raw))


if __name__ == "__main__":
    unittest.main()
