"""The capability set Joi answers real requests with.

Written against the gap list drawn up beside Numen: the actions a companion
needs before "帮我熔点铁" or "工作台怎么做" have any honest answer, and the
boundaries each of them still has to respect.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agent_companion.core.minecraft_contract import (
    GAME_ACTIONS,
    QUERY_ACTIONS,
    MinecraftContractError,
    canonicalize_game_intent,
    check_intent_scope,
)
from agent_companion.core.minecraft_bridge import _safe_query_detail
from agent_companion.core.minecraft_memory import MinecraftWorldMemory
from agent_companion.core.minecraft_skills import MinecraftSkillLibrary
from agent_companion.core.realtime_voice import minecraft_proposal_tools


def _intent(**fields: object) -> dict[str, object]:
    return canonicalize_game_intent({"final": True, "source": "voice", "intent": fields})


SCOPE = {
    "dimensions": ["overworld"],
    "max_radius": 32,
    "max_blocks_changed": 16,
    "allowed_blocks": ["raw_iron", "coal", "oak_log"],
    "allowed_players": ["Steve"],
    "allow_build": False,
    "allow_containers": False,
}


class MinecraftCapabilityContractTests(unittest.TestCase):
    def test_every_action_is_reachable_from_voice(self) -> None:
        """A capability nobody can ask for is not a capability."""

        offered = {tool["function"]["name"][len("minecraft_") :] for tool in minecraft_proposal_tools()}
        for action in GAME_ACTIONS | QUERY_ACTIONS | {"observe_screen"}:
            self.assertIn(action, offered, action)

    def test_the_new_actions_canonicalize_with_usable_defaults(self) -> None:
        self.assertEqual(_intent(action="smelt", item="raw_iron"), {"action": "smelt", "item": "raw_iron", "count": 1, "fuel": ""})
        self.assertEqual(_intent(action="equip", item="stone_pickaxe")["destination"], "hand")
        self.assertEqual(_intent(action="locate", target="village")["kind"], "structure")
        self.assertEqual(_intent(action="fish")["duration_seconds"], 60)
        self.assertEqual(_intent(action="load_skill", name="nether"), {"action": "load_skill", "name": "nether"})

    def test_a_skill_name_can_never_be_a_path(self) -> None:
        for name in ("../etc/passwd", "/tmp/x", "Nether/../..", ""):
            with self.subTest(name=name), self.assertRaises(MinecraftContractError):
                _intent(action="load_skill", name=name)

    def test_storage_actions_need_the_container_permission(self) -> None:
        for action in ({"action": "sort_inventory"}, {"action": "inspect_container"}, {"action": "deposit", "items": [{"item": "oak_log", "count": 1}]}):
            with self.subTest(action=action["action"]):
                self.assertEqual(check_intent_scope(_intent(**action), SCOPE), "containers_not_allowed")
                self.assertEqual(check_intent_scope(_intent(**action), {**SCOPE, "allow_containers": True}), "")

    def test_smelting_and_dropping_stay_inside_the_allowed_blocks(self) -> None:
        self.assertEqual(check_intent_scope(_intent(action="smelt", item="raw_iron", fuel="coal"), SCOPE), "")
        self.assertEqual(check_intent_scope(_intent(action="smelt", item="gold_ore"), SCOPE), "block_out_of_scope")
        self.assertEqual(check_intent_scope(_intent(action="smelt", item="raw_iron", fuel="blaze_rod"), SCOPE), "block_out_of_scope")
        self.assertEqual(check_intent_scope(_intent(action="drop", item="oak_log"), SCOPE), "")
        self.assertEqual(check_intent_scope(_intent(action="drop", item="diamond"), SCOPE), "block_out_of_scope")

    def test_a_lookup_answers_in_words_and_never_in_coordinates(self) -> None:
        self.assertEqual(
            _safe_query_detail({"target": "village", "kind": "structure", "direction": "north", "distance": "far"}),
            "village 在north方向，far",
        )
        self.assertIn("oak_planks 需要4、现有1", _safe_query_detail(
            {"item": "crafting_table", "needs_table": False, "ingredients": [{"name": "oak_planks", "need": 4, "have": 1}]}
        ))
        # Anything that is not a validated name, bearing and band is dropped whole.
        self.assertEqual(_safe_query_detail({"target": "x=133 z=-20", "direction": "north", "distance": "far"}), "")
        self.assertEqual(_safe_query_detail({"container": "chest", "contents": [{"name": "at 12,64", "count": 1}]}), "chest 里：空的")


class MinecraftSkillLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "config" / "minecraft-skills"
        self.root.mkdir(parents=True)
        self.library = MinecraftSkillLibrary(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_notes_are_indexed_by_their_first_line(self) -> None:
        (self.root / "nether.md").write_text("# 下界\n去下界要黑曜石和打火石。\n更多细节……", encoding="utf-8")
        self.assertEqual(self.library.index(), [{"name": "nether", "summary": "下界"}])
        self.assertIn("nether（下界）", self.library.index_text())
        self.assertIn("打火石", self.library.load("nether"))

    def test_an_empty_library_adds_nothing_to_the_prompt(self) -> None:
        self.assertEqual(self.library.index_text(), "")
        self.assertEqual(self.library.load("nether"), "")

    def test_a_note_can_only_come_from_the_notes_folder(self) -> None:
        outside = Path(self.temporary.name) / "secret.md"
        outside.write_text("private", encoding="utf-8")
        for name in ("../secret", "..%2Fsecret", "/etc/hosts", "NETHER/../nether"):
            self.assertEqual(self.library.load(name), "", name)

    def test_an_oversized_note_is_refused_rather_than_truncating_the_prompt(self) -> None:
        (self.root / "huge.md").write_text("# 巨大\n" + ("长" * 40_000), encoding="utf-8")
        self.assertEqual(self.library.load("huge"), "")


class MinecraftWorldMemoryTests(unittest.TestCase):
    def test_joi_remembers_the_furnace_she_already_has(self) -> None:
        """Numen's point: walk back to the workbench instead of building another."""

        with tempfile.TemporaryDirectory() as directory:
            memory = MinecraftWorldMemory(Path(directory))
            memory.remember("srv", "world", observation={"dimension": "overworld"}, recent_goals=[], workstations=["furnace", "chest"])
            memory.remember("srv", "world", observation={"dimension": "overworld"}, recent_goals=[], workstations=["furnace", "crafting_table"])
            summary = memory.summary("srv", "world")
            for station in ("furnace", "chest", "crafting_table"):
                self.assertIn(station, summary)
            # Only real workstations, and the same one is never listed twice.
            memory.remember("srv", "world", observation={}, recent_goals=[], workstations=["dirt", "furnace"])
            self.assertEqual(memory.summary("srv", "world").count("furnace"), 1)
            self.assertNotIn("dirt", memory.summary("srv", "world"))


if __name__ == "__main__":
    unittest.main()
