"""Evidence must stop being usable the moment the screen stops matching it."""

from __future__ import annotations

import time
import unittest

from agent_companion.core.vision.capture_geometry import geometry_for_window, layout_from_displays
from agent_companion.core.vision.capture_geometry import DisplayInfo
from agent_companion.core.vision.target_evidence import (
    CaptureIdentity,
    TargetEvidence,
    evaluate_evidence,
    evidence_from_capture,
    strongest_source,
)


LAPTOP = DisplayInfo(display_id="1", x=0, y=0, width=1440, height=900, backing_scale=2.0, is_main=True)
IDENTITY = CaptureIdentity(
    display_layout_digest="sha256:layout-a",
    display_id="1",
    window_id="win-1",
    app_id="com.apple.Safari",
    scale=2.0,
    capture_digest="sha256:capture-a",
)


def _evidence(**overrides) -> TargetEvidence:
    base = dict(
        target_id="t-1",
        label="下一页",
        source="accessibility",
        confidence=0.92,
        identity=IDENTITY,
        clickable=True,
        enabled=True,
        logical_bounds=(100, 100, 200, 140),
    )
    base.update(overrides)
    return TargetEvidence(**base)


class EvidenceValidityTests(unittest.TestCase):
    def test_fresh_accessibility_evidence_is_actionable(self) -> None:
        verdict = evaluate_evidence(_evidence(), IDENTITY)
        self.assertTrue(verdict.usable)
        self.assertEqual(verdict.reason, "evidence_current")

    def test_missing_evidence_is_never_actionable(self) -> None:
        self.assertFalse(evaluate_evidence(None, IDENTITY).usable)

    def test_expired_evidence_requires_a_fresh_look_not_a_confirmation(self) -> None:
        stale = _evidence(observed_at=time.time() - 120, ttl_seconds=20)
        verdict = evaluate_evidence(stale, IDENTITY)
        self.assertFalse(verdict.usable)
        self.assertFalse(verdict.requires_selection)
        self.assertEqual(verdict.reason, "evidence_expired")

    def test_each_identity_field_invalidates_on_its_own(self) -> None:
        cases = {
            "window_id": CaptureIdentity(**{**IDENTITY.payload(), "window_id": "win-2"}),
            "display_id": CaptureIdentity(**{**IDENTITY.payload(), "display_id": "2"}),
            "display_layout_digest": CaptureIdentity(**{**IDENTITY.payload(), "display_layout_digest": "sha256:layout-b"}),
            "capture_digest": CaptureIdentity(**{**IDENTITY.payload(), "capture_digest": "sha256:capture-b"}),
            "app_id": CaptureIdentity(**{**IDENTITY.payload(), "app_id": "com.apple.Notes"}),
            "scale": CaptureIdentity(**{**IDENTITY.payload(), "scale": 1.0}),
        }
        for field_name, current in cases.items():
            with self.subTest(field=field_name):
                verdict = evaluate_evidence(_evidence(), current)
                self.assertFalse(verdict.usable)
                self.assertEqual(verdict.reason, "capture_identity_changed")
                self.assertIn(field_name, verdict.changed)

    def test_unplugging_a_display_invalidates_evidence_taken_before_it(self) -> None:
        before = layout_from_displays([LAPTOP, DisplayInfo("2", -1920, 0, 1920, 1080, 1.0)])
        after = layout_from_displays([LAPTOP])
        current = CaptureIdentity(**{**IDENTITY.payload(), "display_layout_digest": after.digest()})
        evidence = _evidence(identity=CaptureIdentity(**{**IDENTITY.payload(), "display_layout_digest": before.digest()}))
        self.assertEqual(evaluate_evidence(evidence, current).reason, "capture_identity_changed")

    def test_untrusted_geometry_blocks_action_outright(self) -> None:
        blind = CaptureIdentity(**{**IDENTITY.payload(), "geometry_trusted": False})
        self.assertEqual(evaluate_evidence(_evidence(identity=blind), blind).reason, "geometry_untrusted")

    def test_vision_only_targets_must_be_chosen_by_the_user(self) -> None:
        for source in ("vision", "ocr", "coordinate"):
            with self.subTest(source=source):
                verdict = evaluate_evidence(_evidence(source=source), IDENTITY)
                self.assertFalse(verdict.usable)
                self.assertTrue(verdict.requires_selection)
                self.assertEqual(verdict.reason, "vision_only_target")

    def test_ambiguity_and_low_confidence_ask_rather_than_act(self) -> None:
        self.assertTrue(evaluate_evidence(_evidence(ambiguity="multiple_matches"), IDENTITY).requires_selection)
        self.assertTrue(evaluate_evidence(_evidence(alternatives=3), IDENTITY).requires_selection)
        self.assertEqual(evaluate_evidence(_evidence(confidence=0.4), IDENTITY).reason, "low_confidence")

    def test_a_disabled_control_is_not_clicked(self) -> None:
        self.assertEqual(evaluate_evidence(_evidence(enabled=False), IDENTITY).reason, "target_not_actionable")
        self.assertEqual(evaluate_evidence(_evidence(clickable=False), IDENTITY).reason, "target_not_actionable")

    def test_evidence_without_bounds_cannot_produce_a_coordinate(self) -> None:
        self.assertEqual(evaluate_evidence(_evidence(logical_bounds=None), IDENTITY).reason, "no_logical_bounds")


class EvidenceDerivationTests(unittest.TestCase):
    def test_logical_bounds_come_from_the_capture_geometry(self) -> None:
        geometry = geometry_for_window(layout_from_displays([LAPTOP]), (100, 50, 400, 300))
        evidence = evidence_from_capture("t-1", "确定", "accessibility", 0.9, geometry, (40, 20, 120, 60), IDENTITY)
        # Capture pixels at 2x map back to points offset by the window origin.
        self.assertEqual(evidence.logical_bounds, (120, 60, 160, 80))
        self.assertEqual(evidence.logical_center, (140, 70))
        self.assertEqual(evidence.pixel_bounds, (40, 20, 120, 60))

    def test_digest_tracks_both_the_target_and_its_conditions(self) -> None:
        first = _evidence()
        same = _evidence()
        moved = _evidence(logical_bounds=(300, 300, 400, 340))
        elsewhere = _evidence(identity=CaptureIdentity(**{**IDENTITY.payload(), "window_id": "win-9"}))
        self.assertEqual(first.evidence_digest, same.evidence_digest)
        self.assertNotEqual(first.evidence_digest, moved.evidence_digest)
        self.assertNotEqual(first.evidence_digest, elsewhere.evidence_digest)


class PerceptionPriorityTests(unittest.TestCase):
    def test_the_strongest_available_source_wins(self) -> None:
        self.assertEqual(strongest_source(["ocr", "accessibility", "vision"]), "accessibility")
        self.assertEqual(strongest_source(["ocr", "vision"]), "ocr")
        self.assertEqual(strongest_source(["application", "accessibility"]), "application")
        self.assertEqual(strongest_source([]), "")


if __name__ == "__main__":
    unittest.main()
