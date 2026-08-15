from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agent_companion.core.minecraft_memory import MinecraftWorldMemory


class MinecraftWorldMemoryTests(unittest.TestCase):
    def test_remember_and_summary_are_bounded_and_coordinate_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = MinecraftWorldMemory(Path(directory))
            memory.remember(
                "local-hmcl",
                "world",
                observation={
                    "dimension": "overworld",
                    "health": 16,
                    "food": 8,
                    "world": {"time_of_day": 12000, "raining": True},
                },
                recent_goals=["minecraft.collect", "minecraft.craft"],
            )
            summary = memory.summary("local-hmcl", "world")
            self.assertIn("overworld", summary)
            self.assertIn("血量 16", summary)
            self.assertIn("minecraft.collect", summary)
            for forbidden in ("position", '"x"', "坐标"):
                self.assertNotIn(forbidden, summary)
            self.assertEqual(memory.summary("other-server", "world"), "")
            path = memory._path
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_recent_goals_are_capped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = MinecraftWorldMemory(Path(directory))
            memory.remember("s", "w", observation={"dimension": "overworld"}, recent_goals=[f"goal-{i}" for i in range(30)])
            summary = memory.summary("s", "w")
            self.assertIn("goal-29", summary)
            self.assertNotIn("goal-0", summary)


if __name__ == "__main__":
    unittest.main()
