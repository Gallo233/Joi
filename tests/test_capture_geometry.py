"""Multi-display coordinate maths, checked against synthetic arrangements.

Real hardware verification is still owed (TDD §9.1 asks for a device matrix),
but the cases that actually break -- a display left of the main one, a Retina
laptop beside a 1x monitor, a window straddling both -- are pure geometry and
can be pinned down here.
"""

from __future__ import annotations

import unittest

from agent_companion.core.vision.capture_geometry import (
    DisplayInfo,
    DisplayLayout,
    geometry_for_window,
    layout_from_displays,
    probe_display_layout,
)


LAPTOP = DisplayInfo(display_id="1", x=0, y=0, width=1440, height=900, backing_scale=2.0, is_main=True)
# A 1x monitor placed to the LEFT of the laptop, so its origin is negative.
EXTERNAL_LEFT = DisplayInfo(display_id="2", x=-1920, y=0, width=1920, height=1080, backing_scale=1.0)


class DisplayLayoutTests(unittest.TestCase):
    def test_a_window_is_attributed_to_the_display_it_mostly_covers(self) -> None:
        layout = layout_from_displays([LAPTOP, EXTERNAL_LEFT])
        self.assertEqual(layout.display_for_rect((100, 100, 400, 300)).display_id, "1")
        self.assertEqual(layout.display_for_rect((-1800, 100, 400, 300)).display_id, "2")

        # Straddling: the corner is on the external display, the bulk is not.
        straddling = (-100, 100, 400, 300)
        self.assertEqual(layout.display_for_rect(straddling).display_id, "1")

    def test_a_window_on_no_known_display_is_not_guessed(self) -> None:
        layout = layout_from_displays([LAPTOP])
        self.assertIsNone(layout.display_for_rect((5000, 5000, 200, 200)))

    def test_mixed_scale_is_visible_to_callers(self) -> None:
        self.assertTrue(layout_from_displays([LAPTOP, EXTERNAL_LEFT]).mixed_scale)
        self.assertFalse(layout_from_displays([LAPTOP]).mixed_scale)

    def test_digest_changes_when_the_arrangement_changes(self) -> None:
        single = layout_from_displays([LAPTOP])
        added = layout_from_displays([LAPTOP, EXTERNAL_LEFT])
        moved = layout_from_displays([LAPTOP, DisplayInfo("2", 1440, 0, 1920, 1080, 1.0)])
        rescaled = layout_from_displays([DisplayInfo("1", 0, 0, 1440, 900, 1.0, True)])

        digests = {single.digest(), added.digest(), moved.digest(), rescaled.digest()}
        self.assertEqual(len(digests), 4, "each arrangement must be distinguishable")
        # Stable for the same arrangement, regardless of ordering.
        self.assertEqual(added.digest(), layout_from_displays([EXTERNAL_LEFT, LAPTOP]).digest())


class CaptureGeometryTests(unittest.TestCase):
    def test_retina_window_maps_points_to_capture_pixels(self) -> None:
        layout = layout_from_displays([LAPTOP])
        geometry = geometry_for_window(layout, (100, 50, 400, 300))
        self.assertTrue(geometry.trusted)
        self.assertEqual(geometry.scale, 2.0)
        # The capture starts at the window origin, so the window's own top-left
        # is (0, 0) in capture space, not (100, 50).
        self.assertEqual(geometry.logical_to_capture(100, 50), (0, 0))
        self.assertEqual(geometry.logical_to_capture(300, 200), (400, 300))
        self.assertEqual(geometry.capture_to_logical(400, 300), (300, 200))

    def test_a_one_x_display_left_of_main_keeps_its_own_scale(self) -> None:
        layout = layout_from_displays([LAPTOP, EXTERNAL_LEFT])
        geometry = geometry_for_window(layout, (-1800, 100, 800, 600))
        self.assertEqual(geometry.display_id, "2")
        # The neighbouring Retina display must not lend its scale.
        self.assertEqual(geometry.scale, 1.0)
        self.assertEqual(geometry.logical_to_capture(-1800, 100), (0, 0))
        self.assertEqual(geometry.logical_to_capture(-1400, 400), (400, 300))
        # Negative origins survive the round trip.
        self.assertEqual(geometry.capture_to_logical(400, 300), (-1400, 400))

    def test_round_trip_is_stable_across_both_displays(self) -> None:
        layout = layout_from_displays([LAPTOP, EXTERNAL_LEFT])
        for rect, point in (((0, 0, 1440, 900), (720, 450)), ((-1920, 0, 1920, 1080), (-960, 540))):
            geometry = geometry_for_window(layout, rect)
            captured = geometry.logical_to_capture(*point)
            self.assertEqual(geometry.capture_to_logical(*captured), point)

    def test_untrusted_layout_produces_untrusted_geometry(self) -> None:
        unknown = DisplayLayout((), trusted=False, source="unavailable")
        geometry = geometry_for_window(unknown, (0, 0, 800, 600))
        self.assertFalse(geometry.trusted)
        self.assertEqual(geometry.untrusted_reason, "display_layout_untrusted")

    def test_window_outside_every_known_display_is_untrusted(self) -> None:
        geometry = geometry_for_window(layout_from_displays([LAPTOP]), (9000, 9000, 400, 300))
        self.assertFalse(geometry.trusted)
        self.assertEqual(geometry.untrusted_reason, "window_outside_known_displays")

    def test_scale_is_never_derived_from_the_main_display_for_another_one(self) -> None:
        # The regression this whole module exists for: one global ratio applied
        # everywhere put the crop box on the wrong screen.
        layout = layout_from_displays([LAPTOP, EXTERNAL_LEFT])
        main_scale = layout.main.backing_scale
        external = geometry_for_window(layout, (-1920, 0, 1920, 1080))
        self.assertNotEqual(external.scale, main_scale)
        self.assertEqual(external.scale, EXTERNAL_LEFT.backing_scale)


class ProbeTests(unittest.TestCase):
    def test_more_than_one_display_without_quartz_is_untrusted(self) -> None:
        class Completed:
            stdout = '{"SPDisplaysDataType": [{"spdisplays_ndrvs": [{"_name": "A"}, {"_name": "B"}]}]}'

        layout = probe_display_layout(runner=lambda *args, **kwargs: Completed())
        self.assertFalse(layout.trusted)
        self.assertEqual(layout.source, "display_count_only")
        self.assertIn("2 displays", layout.note)

    def test_a_failing_probe_is_untrusted_rather_than_assumed_single(self) -> None:
        def explode(*args: object, **kwargs: object):
            raise OSError("system_profiler missing")

        layout = probe_display_layout(runner=explode)
        self.assertFalse(layout.trusted)
        self.assertEqual(layout.displays, ())

    def test_probe_on_this_machine_never_claims_false_confidence(self) -> None:
        layout = probe_display_layout()
        # Either Quartz measured it, or we admit we do not know.
        self.assertTrue(layout.trusted == bool(layout.displays))


if __name__ == "__main__":
    unittest.main()
