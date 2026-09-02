"""The capture path must crop from the display the window is actually on.

`observe()` shells out to `screencapture` and hides the companion window, so
those edges are faked here. Everything between -- which display is chosen, what
`-D` index is passed, where the crop box lands, and what scale is recorded --
is the real code, because that is where a second display goes wrong silently.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from agent_companion.core.tools.targeting import click_arguments_from_state
from agent_companion.core.vision.capture_geometry import DisplayInfo, DisplayLayout, DisplayLayoutCache, layout_from_displays
from agent_companion.core.vision.mac import MacScreenObserver


LAPTOP = DisplayInfo(display_id="1", x=0, y=0, width=1440, height=900, backing_scale=2.0, is_main=True, capture_index=1)
# 1x monitor to the LEFT of the laptop, so its logical origin is negative.
EXTERNAL_LEFT = DisplayInfo(display_id="2", x=-1920, y=0, width=1920, height=1080, backing_scale=1.0, capture_index=2)


class CapturePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.commands: list[list[str]] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _observe(self, layout: DisplayLayout, window_rect: tuple[int, int, int, int] | None, display_pixels: tuple[int, int]):
        """Run observe() against a faked screen of a given pixel size."""

        def fake_screencapture(command, **kwargs):
            self.commands.append(list(command))
            Image.new("RGB", display_pixels, "white").save(command[-1], "PNG")

            class Completed:
                returncode = 0

            return Completed()

        observer = MacScreenObserver(self.workspace, layout_cache=DisplayLayoutCache(prober=lambda: layout))
        with patch("agent_companion.core.vision.mac.subprocess.run", side_effect=fake_screencapture), patch(
            "agent_companion.core.vision.mac.hide_foreground_companion_window", return_value=0
        ), patch("agent_companion.core.vision.mac.restore_window"), patch(
            "agent_companion.core.vision.mac.foreground_window", return_value=42
        ), patch("agent_companion.core.vision.mac.window_title", return_value="Safari"), patch.object(
            MacScreenObserver, "_active_window_rect", staticmethod(lambda: window_rect)
        ):
            return observer.observe("active_window")

    def test_a_window_on_the_main_display_is_cropped_at_its_own_scale(self) -> None:
        observation = self._observe(layout_from_displays([LAPTOP, EXTERNAL_LEFT]), (100, 50, 400, 300), (2880, 1800))

        self.assertIn("-D", self.commands[0])
        self.assertEqual(self.commands[0][self.commands[0].index("-D") + 1], "1")
        rect = observation.capture_rect
        self.assertEqual((rect.screen_x, rect.screen_y, rect.width, rect.height), (100, 50, 400, 300))
        self.assertEqual(rect.capture_scale, 2.0)
        self.assertEqual(rect.display_id, "1")
        self.assertTrue(rect.geometry_trusted)
        # 400x300 points at 2x is 800x600 pixels.
        self.assertEqual((observation.width, observation.height), (800, 600))

    def test_a_window_on_the_external_display_uses_that_display(self) -> None:
        observation = self._observe(layout_from_displays([LAPTOP, EXTERNAL_LEFT]), (-1800, 100, 800, 600), (1920, 1080))

        # Captured from display 2, not cropped out of the main display's image.
        self.assertEqual(self.commands[0][self.commands[0].index("-D") + 1], "2")
        rect = observation.capture_rect
        self.assertEqual(rect.display_id, "2")
        # The neighbouring Retina display must not lend its scale.
        self.assertEqual(rect.capture_scale, 1.0)
        self.assertEqual((rect.screen_x, rect.screen_y), (-1800, 100))
        # Negative origin is subtracted before scaling, so the crop is 800x600.
        self.assertEqual((observation.width, observation.height), (800, 600))

    def test_the_regression_this_exists_for(self) -> None:
        # With the old code the external window's crop box was computed from the
        # main display's 2x scale against the main display's image, which put it
        # far outside the picture. Confirm the new path keeps it in range.
        observation = self._observe(layout_from_displays([LAPTOP, EXTERNAL_LEFT]), (-1920, 0, 1920, 1080), (1920, 1080))
        self.assertTrue(observation.capture_rect.geometry_trusted)
        self.assertEqual((observation.width, observation.height), (1920, 1080))

    def test_an_unmeasured_layout_reports_untrusted_geometry(self) -> None:
        blind = DisplayLayout((), trusted=False, source="unavailable")
        with patch.object(MacScreenObserver, "_screen_dimensions", staticmethod(lambda: (1440, 900))):
            observation = self._observe(blind, (100, 50, 400, 300), (2880, 1800))
        self.assertFalse(observation.capture_rect.geometry_trusted)
        # No -D is passed when we do not know which display is which.
        self.assertNotIn("-D", self.commands[0])

    def test_a_crop_that_had_to_be_clamped_is_not_reported_as_the_window(self) -> None:
        # Window claims to extend past the captured display.
        observation = self._observe(layout_from_displays([LAPTOP]), (1300, 800, 800, 600), (2880, 1800))
        rect = observation.capture_rect
        self.assertFalse(rect.geometry_trusted, "a clamped crop is a different picture, not a smaller one")


class UntrustedGeometryBlocksClicksTests(unittest.TestCase):
    def _state(self, *, trusted: bool) -> dict:
        return {
            "width": 800,
            "height": 600,
            "capture_rect": {
                "screen_x": 100,
                "screen_y": 50,
                "width": 400,
                "height": 300,
                "capture_scale": 2.0,
                "scale_x": 2.0,
                "scale_y": 2.0,
                "display_id": "1",
                "geometry_trusted": trusted,
            },
        }

    def test_a_trusted_capture_yields_a_click_point(self) -> None:
        candidate = {"bbox": [100, 100, 40, 20]}
        self.assertIsNotNone(click_arguments_from_state(candidate, self._state(trusted=True)))

    def test_an_untrusted_capture_yields_no_click_point(self) -> None:
        candidate = {"bbox": [100, 100, 40, 20]}
        self.assertIsNone(click_arguments_from_state(candidate, self._state(trusted=False)))


if __name__ == "__main__":
    unittest.main()
