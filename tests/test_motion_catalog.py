"""What a character can be asked to perform, and the words that ask for it.

The Shell needs both halves to offer a motion: the label to show and the phrase
Core actually matches. Both live in `character_motion.py`, and rebuilding the
table in the Shell would let a renamed motion keep a button that triggers
nothing -- worse than no button, because it looks like the character is broken.
"""

from __future__ import annotations

import unittest

from agent_companion.core.character_motion import MOTION_SPECS, character_motion_from_text, motion_catalog


class MotionCatalogTests(unittest.TestCase):
    def test_every_offered_phrase_actually_triggers_its_motion(self) -> None:
        catalog = motion_catalog([
            {"id": name, "animation_url": f"https://example.invalid/{name}.vrma"}
            for name in MOTION_SPECS
        ])
        self.assertTrue(catalog)
        for entry in catalog:
            self.assertEqual(
                character_motion_from_text(entry["trigger"]),
                entry["motion"],
                f"offering {entry['trigger']!r} for {entry['motion']} would do nothing",
            )
            self.assertEqual(entry["label"], MOTION_SPECS[entry["motion"]].label)

    def test_only_motions_the_character_can_perform_are_offered(self) -> None:
        catalog = motion_catalog([
            {"id": "dance", "animation_url": "https://example.invalid/dance.vmd"},
            {"id": "greet", "motion_group": "FlickUp"},
            # No clip and no Live2D group: a reply with no movement.
            {"id": "happy"},
            {"id": "not-a-motion", "animation_url": "https://example.invalid/x.vrma"},
        ])
        self.assertEqual([row["motion"] for row in catalog], ["dance", "greet"])

    def test_idle_is_a_resting_state_not_something_to_ask_for(self) -> None:
        catalog = motion_catalog([{"id": "idle", "motion_group": "Idle", "loop": True}])
        self.assertEqual(catalog, [])

    def test_a_character_with_no_bindings_offers_nothing(self) -> None:
        for empty in ([], None, "not a list", [None, 7, {}]):
            self.assertEqual(motion_catalog(empty), [])

    def test_duplicate_bindings_are_offered_once(self) -> None:
        catalog = motion_catalog([
            {"id": "dance", "animation_url": "https://example.invalid/a.vmd"},
            {"id": "dance", "animation_url": "https://example.invalid/b.vmd"},
        ])
        self.assertEqual(len(catalog), 1)


if __name__ == "__main__":
    unittest.main()
