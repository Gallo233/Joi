"""Per-display coordinate geometry.

A screenshot, a window rectangle and a click all live in different spaces:

- **logical** -- macOS points in the global desktop space. Origins can be
  negative when a display sits left of or above the main one.
- **pixel** -- the backing store of one display. A Retina laptop next to a
  1x external monitor has two different scales at the same instant.
- **capture** -- offsets inside one screenshot image, whose origin is the
  captured rectangle rather than the desktop.

The previous implementation collapsed all three by dividing the screenshot
width by the *main* display's width and applying that ratio everywhere. On a
single built-in display that happens to be right; with a second display it puts
the crop box in the wrong place, and clamping then hides the error by silently
falling back to a full-screen capture. Joi would be looking at one display and
clicking coordinates belonging to another.

So scale is a property of a display, never of the session, and a layout that
cannot be measured is reported as untrusted rather than guessed. Callers must
refuse to act on untrusted geometry (TDD §9.1, §9.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import subprocess
import time
from typing import Any, Iterable


Rect = tuple[int, int, int, int]  # x, y, width, height


@dataclass(frozen=True)
class DisplayInfo:
    """One display's logical placement and its own backing scale."""

    display_id: str
    x: int
    y: int
    width: int
    height: int
    backing_scale: float = 1.0
    is_main: bool = False
    # 1-based position in the active display list, which is what
    # `screencapture -D` expects. 0 means "unknown, capture the main display".
    capture_index: int = 0

    @property
    def bounds(self) -> Rect:
        return (self.x, self.y, self.width, self.height)

    def contains_point(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    def overlap_area(self, rect: Rect) -> int:
        rx, ry, rw, rh = rect
        left = max(self.x, rx)
        top = max(self.y, ry)
        right = min(self.x + self.width, rx + rw)
        bottom = min(self.y + self.height, ry + rh)
        return max(0, right - left) * max(0, bottom - top)

    def payload(self) -> dict[str, Any]:
        return {
            "display_id": self.display_id,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "backing_scale": round(float(self.backing_scale), 4),
            "is_main": self.is_main,
            "capture_index": self.capture_index,
        }


@dataclass(frozen=True)
class DisplayLayout:
    """The set of displays as observed, plus whether we actually know it."""

    displays: tuple[DisplayInfo, ...] = ()
    trusted: bool = False
    source: str = "unknown"
    note: str = ""

    @property
    def main(self) -> DisplayInfo | None:
        for display in self.displays:
            if display.is_main:
                return display
        return self.displays[0] if self.displays else None

    @property
    def mixed_scale(self) -> bool:
        return len({round(float(display.backing_scale), 3) for display in self.displays}) > 1

    def digest(self) -> str:
        """Identity of the whole arrangement.

        Any change -- a display added, removed, moved or rescaled -- produces a
        new digest, which is what invalidates evidence captured before it.
        """
        payload = [display.payload() for display in sorted(self.displays, key=lambda item: item.display_id)]
        encoded = json.dumps({"displays": payload, "trusted": self.trusted}, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]

    def display_for_rect(self, rect: Rect) -> DisplayInfo | None:
        """The display a window mostly sits on.

        Windows straddle displays, so the largest overlap wins rather than the
        top-left corner, which can belong to a neighbour.
        """
        if not self.displays:
            return None
        best = max(self.displays, key=lambda display: display.overlap_area(rect))
        return best if best.overlap_area(rect) > 0 else None

    def display_for_point(self, x: int, y: int) -> DisplayInfo | None:
        for display in self.displays:
            if display.contains_point(x, y):
                return display
        return None

    def payload(self) -> dict[str, Any]:
        return {
            "displays": [display.payload() for display in self.displays],
            "trusted": self.trusted,
            "source": self.source,
            "note": self.note,
            "mixed_scale": self.mixed_scale,
            "digest": self.digest(),
        }


@dataclass(frozen=True)
class CaptureGeometry:
    """Maps between logical points, display pixels and one capture image.

    ``origin`` is the captured rectangle in logical points; ``scale`` is the
    display's own backing scale. Nothing here consults any other display.
    """

    display_id: str
    origin_x: int
    origin_y: int
    logical_width: int
    logical_height: int
    scale: float
    pixel_width: int = 0
    pixel_height: int = 0
    trusted: bool = True
    untrusted_reason: str = ""

    def logical_to_capture(self, x: int, y: int) -> tuple[int, int]:
        """Global point -> offset inside the capture image, in pixels."""
        return (
            int(round((x - self.origin_x) * self.scale)),
            int(round((y - self.origin_y) * self.scale)),
        )

    def capture_to_logical(self, x: int, y: int) -> tuple[int, int]:
        """Offset inside the capture image -> global point."""
        return (
            int(round(x / self.scale)) + self.origin_x,
            int(round(y / self.scale)) + self.origin_y,
        )

    def capture_bbox_to_logical(self, bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        left, top = self.capture_to_logical(bbox[0], bbox[1])
        right, bottom = self.capture_to_logical(bbox[2], bbox[3])
        return (left, top, right, bottom)

    def contains_capture_point(self, x: int, y: int) -> bool:
        return 0 <= x < max(1, self.pixel_width or int(self.logical_width * self.scale)) and 0 <= y < max(
            1, self.pixel_height or int(self.logical_height * self.scale)
        )

    def payload(self) -> dict[str, Any]:
        return {
            "display_id": self.display_id,
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
            "logical_width": self.logical_width,
            "logical_height": self.logical_height,
            "scale": round(float(self.scale), 4),
            "pixel_width": self.pixel_width,
            "pixel_height": self.pixel_height,
            "trusted": self.trusted,
            "untrusted_reason": self.untrusted_reason,
        }


def geometry_for_window(layout: DisplayLayout, window_rect: Rect, *, pixel_size: tuple[int, int] | None = None) -> CaptureGeometry:
    """Build the mapping for a window capture, refusing to guess.

    When the layout is untrusted, or the window does not sit on any known
    display, the result is marked untrusted so the caller fails closed instead
    of clicking a coordinate derived from an assumed scale.
    """
    x, y, width, height = window_rect
    display = layout.display_for_rect(window_rect)
    if not layout.trusted or display is None:
        reason = "display_layout_untrusted" if not layout.trusted else "window_outside_known_displays"
        fallback = layout.main
        return CaptureGeometry(
            display_id=display.display_id if display else (fallback.display_id if fallback else "unknown"),
            origin_x=x,
            origin_y=y,
            logical_width=max(1, width),
            logical_height=max(1, height),
            scale=float(display.backing_scale if display else (fallback.backing_scale if fallback else 1.0)),
            pixel_width=int(pixel_size[0]) if pixel_size else 0,
            pixel_height=int(pixel_size[1]) if pixel_size else 0,
            trusted=False,
            untrusted_reason=reason,
        )
    return CaptureGeometry(
        display_id=display.display_id,
        origin_x=x,
        origin_y=y,
        logical_width=max(1, width),
        logical_height=max(1, height),
        scale=float(display.backing_scale),
        pixel_width=int(pixel_size[0]) if pixel_size else int(width * display.backing_scale),
        pixel_height=int(pixel_size[1]) if pixel_size else int(height * display.backing_scale),
        trusted=True,
    )


def probe_display_layout(runner: Any = None) -> DisplayLayout:
    """Measure the current arrangement, or say that we could not.

    Quartz gives exact bounds and per-display scale. Without PyObjC we can still
    tell *how many* displays exist, which is enough to know when a single-display
    assumption would be a lie -- so that case is reported untrusted rather than
    silently treated as one display.
    """
    quartz = _probe_via_quartz()
    if quartz is not None:
        return quartz
    return _probe_without_quartz(runner or subprocess.run)


def _probe_via_quartz() -> DisplayLayout | None:
    try:
        import Quartz  # type: ignore
    except Exception:
        return None
    try:
        error, display_ids, _count = Quartz.CGGetActiveDisplayList(16, None, None)
        if error != 0 or not display_ids:
            return None
        main_id = Quartz.CGMainDisplayID()
        displays: list[DisplayInfo] = []
        for index, display_id in enumerate(display_ids, start=1):
            bounds = Quartz.CGDisplayBounds(display_id)
            mode = Quartz.CGDisplayCopyDisplayMode(display_id)
            logical_width = int(bounds.size.width) or 1
            pixel_width = int(Quartz.CGDisplayModeGetPixelWidth(mode)) if mode else logical_width
            displays.append(
                DisplayInfo(
                    display_id=str(display_id),
                    x=int(bounds.origin.x),
                    y=int(bounds.origin.y),
                    width=logical_width,
                    height=int(bounds.size.height) or 1,
                    backing_scale=round(pixel_width / logical_width, 4) if logical_width else 1.0,
                    is_main=display_id == main_id,
                    capture_index=index,
                )
            )
        return DisplayLayout(tuple(displays), trusted=bool(displays), source="quartz")
    except Exception:
        return None


def _probe_without_quartz(runner: Any) -> DisplayLayout:
    count = _display_count(runner)
    if count > 1:
        # We know there is more than one display but not where they are or what
        # they scale at. Guessing here is how clicks land on the wrong screen.
        return DisplayLayout(
            (),
            trusted=False,
            source="display_count_only",
            note=f"{count} displays detected but geometry needs PyObjC (pyobjc-framework-Quartz)",
        )
    return DisplayLayout(
        (),
        trusted=False,
        source="unavailable",
        note="display geometry needs PyObjC (pyobjc-framework-Quartz)",
    )


def _display_count(runner: Any) -> int:
    try:
        completed = runner(
            ["system_profiler", "-json", "SPDisplaysDataType"],
            capture_output=True,
            text=True,
            timeout=15.0,
        )
    except Exception:
        return 0
    try:
        payload = json.loads(getattr(completed, "stdout", "") or "{}")
    except (TypeError, ValueError):
        return 0
    return _count_displays(payload.get("SPDisplaysDataType"))


def _count_displays(nodes: Any) -> int:
    if not isinstance(nodes, list):
        return 0
    total = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        attached = node.get("spdisplays_ndrvs")
        if isinstance(attached, list):
            total += len(attached)
    return total


def layout_from_displays(displays: Iterable[DisplayInfo], *, trusted: bool = True, source: str = "explicit") -> DisplayLayout:
    """Build a layout directly -- used by tests and by injected probes."""
    return DisplayLayout(tuple(displays), trusted=trusted, source=source)


class DisplayLayoutCache:
    """Re-probes the arrangement, but not on every single screenshot.

    The Quartz path is cheap; the fallback shells out to `system_profiler` and
    costs about a second, which is far too slow per observation. A short TTL
    keeps observation responsive while still noticing a display being plugged
    in within a few seconds -- and evidence carries the layout digest, so a
    change that slips through the window invalidates the target rather than
    producing a stale coordinate.
    """

    def __init__(self, ttl_seconds: float = 5.0, prober: Any = None) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._prober = prober or probe_display_layout
        self._layout: DisplayLayout | None = None
        self._probed_at = 0.0

    def get(self, now: float | None = None, *, force: bool = False) -> DisplayLayout:
        moment = time.time() if now is None else float(now)
        if force or self._layout is None or moment - self._probed_at >= self.ttl_seconds:
            self._layout = self._prober()
            self._probed_at = moment
        return self._layout

    def invalidate(self) -> None:
        self._layout = None
        self._probed_at = 0.0
