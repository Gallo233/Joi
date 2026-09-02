"""macOS Screen Observer using native screencapture.

Captures the active window or full screen, handles Retina scaling,
and produces VisionObservation for downstream targeting and OCR.
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from agent_companion.core.vision.capture_geometry import DisplayInfo, DisplayLayout, DisplayLayoutCache
from agent_companion.core.vision.schemas import CaptureRect, VisionObservation
from agent_companion.core.windows_focus_mac import (
    foreground_window,
    hide_foreground_companion_window,
    is_companion_window,
    is_visible_window,
    restore_window,
    window_title,
)


class MacScreenObserver:
    def __init__(self, workspace: Path, layout_cache: DisplayLayoutCache | None = None) -> None:
        self.workspace = workspace.resolve()
        self.output_dir = self.workspace / "data" / "agent_companion" / "vision"
        self.layout_cache = layout_cache or DisplayLayoutCache()

    def _display_frame(
        self,
        display: DisplayInfo | None,
        layout: DisplayLayout,
        image_width: int,
        image_height: int,
    ) -> tuple[tuple[int, int], tuple[int, int], float, bool]:
        """Origin, logical size, scale and whether any of it is measured.

        Without a measured layout the only thing left is the main display's
        logical size, which is exactly the assumption that breaks on a second
        display -- so the result is returned as untrusted.
        """
        if display is not None and layout.trusted:
            scale = float(display.backing_scale) or 1.0
            return (display.x, display.y), (display.width, display.height), scale, True
        screen_w, screen_h = self._screen_dimensions()
        scale = image_width / screen_w if screen_w > 0 else 1.0
        return (0, 0), (screen_w, screen_h), scale, False

    def observe(self, target: str = "active_window", query: str = "") -> VisionObservation:
        target = self._normalize_target(target)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        temp_full_path = self.output_dir / f"full_{timestamp}.png"
        output_path = self.output_dir / f"{target}_{timestamp}.png"

        original_hwnd = foreground_window() if target == "active_window" else 0

        # Get window rect BEFORE hiding (so we capture the right window)
        original_rect = self._active_window_rect() if target == "active_window" else None
        hwnd = original_hwnd
        layout = self.layout_cache.get()

        # Hide Joi to see what's behind her
        hidden_hwnd = hide_foreground_companion_window(settle_seconds=0.30)
        try:
            # Re-fetch the window rect after hiding Joi (the frontmost window
            # changed) so the capture and the geometry describe the same window.
            if target == "active_window":
                hwnd = foreground_window()
                title = window_title(hwnd) if hwnd else ""
                screen_rect = self._active_window_rect() or original_rect
            else:
                title = "Desktop"
                screen_rect = None

            # `screencapture` writes one display at a time, so capture the
            # display the window actually sits on. Cropping a second display's
            # window out of the main display's image is how coordinates end up
            # pointing at whatever happens to occupy that spot on the main one.
            display = layout.display_for_rect(screen_rect) if (layout.trusted and screen_rect) else layout.main
            command = ["screencapture", "-x", "-C"]
            if display is not None and display.capture_index > 0:
                command += ["-D", str(display.capture_index)]
            result = subprocess.run(command + [str(temp_full_path)], capture_output=True, timeout=10.0)
            if result.returncode != 0 or not temp_full_path.exists():
                raise RuntimeError(f"screencapture failed (rc={result.returncode})")

            with Image.open(temp_full_path) as img:
                img_width, img_height = img.size
                # Scale belongs to this display. Deriving one ratio from the
                # main display and applying it everywhere is wrong the moment a
                # second display has a different backing scale.
                display_origin, display_size, scale_factor, trusted = self._display_frame(display, layout, img_width, img_height)
                geometry_digest = layout.digest()
                display_id = display.display_id if display else ""

                if target == "active_window" and screen_rect:
                    x, y, w, h = screen_rect
                    # Window points are global; the capture starts at this
                    # display's origin, so subtract it before scaling.
                    crop_box = (
                        int((x - display_origin[0]) * scale_factor),
                        int((y - display_origin[1]) * scale_factor),
                        int((x - display_origin[0] + w) * scale_factor),
                        int((y - display_origin[1] + h) * scale_factor),
                    )
                    clamped = (
                        max(0, min(img_width, crop_box[0])),
                        max(0, min(img_height, crop_box[1])),
                        max(0, min(img_width, crop_box[2])),
                        max(0, min(img_height, crop_box[3])),
                    )
                    crop_w = clamped[2] - clamped[0]
                    crop_h = clamped[3] - clamped[1]
                    # A crop that had to be clamped away is not a smaller view of
                    # the window, it is a different picture. Fall back to the
                    # whole display and say the geometry is not trustworthy,
                    # rather than silently returning the wrong region.
                    heavily_clamped = clamped != crop_box
                    if crop_w < 10 or crop_h < 10 or heavily_clamped:
                        img.save(output_path, "PNG")
                        capture_rect = CaptureRect(
                            screen_x=display_origin[0], screen_y=display_origin[1],
                            width=display_size[0], height=display_size[1],
                            capture_scale=scale_factor, scale_x=scale_factor, scale_y=scale_factor,
                            display_id=display_id,
                            display_layout_digest=geometry_digest,
                            geometry_trusted=trusted and not heavily_clamped,
                        )
                    else:
                        cropped = img.crop(clamped)
                        cropped.save(output_path, "PNG")
                        capture_rect = CaptureRect(
                            screen_x=x, screen_y=y,
                            width=w, height=h,
                            capture_scale=scale_factor,
                            scale_x=crop_w / w if w > 0 else scale_factor,
                            scale_y=crop_h / h if h > 0 else scale_factor,
                            display_id=display_id,
                            display_layout_digest=geometry_digest,
                            geometry_trusted=trusted,
                        )
                        img_width, img_height = cropped.size
                else:
                    img.save(output_path, "PNG")
                    capture_rect = CaptureRect(
                        screen_x=display_origin[0], screen_y=display_origin[1],
                        width=display_size[0], height=display_size[1],
                        capture_scale=scale_factor, scale_x=scale_factor, scale_y=scale_factor,
                        display_id=display_id,
                        display_layout_digest=geometry_digest,
                        geometry_trusted=trusted,
                    )

        finally:
            # Restore Joi companion window visibility
            restore_window(hidden_hwnd)
            if temp_full_path.exists():
                try:
                    temp_full_path.unlink()
                except Exception:
                    pass

        return VisionObservation(
            target=target,
            screenshot_path=output_path,
            screenshot_rel=self._rel(output_path),
            width=img_width,
            height=img_height,
            title=title if target == "active_window" else "Desktop",
            window_handle=hwnd if target == "active_window" else None,
            capture_rect=capture_rect,
            query=query,
        )

    @staticmethod
    def _normalize_target(target: str) -> str:
        value = (target or "active_window").strip().casefold()
        if value in {"fullscreen", "full_screen", "screen", "desktop"}:
            return "fullscreen"
        return "active_window"

    @staticmethod
    def _active_window_rect() -> tuple[int, int, int, int] | None:
        """Fetch frontmost active window coordinates [x, y, width, height] via AppleScript.

        Returns coordinates in macOS logical points (not physical pixels).
        System Events bounds are: left, top, right, bottom (in points).
        """
        script = """
        tell application "System Events"
            try
                set frontmostProcess to first process whose frontmost is true
                if (count of windows of frontmostProcess) > 0 then
                    set win to first window of frontmostProcess
                    set winBounds to bounds of win
                    return item 1 of winBounds & "," & item 2 of winBounds & "," & item 3 of winBounds & "," & item 4 of winBounds
                end if
            end try
        end tell
        return ""
        """
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3.0,
            )
            res = proc.stdout.strip()
            if res:
                parts = [int(p) for p in res.split(",")]
                if len(parts) == 4:
                    left, top, right, bottom = parts
                    width = right - left
                    height = bottom - top
                    if width > 0 and height > 0:
                        return (left, top, width, height)
        except Exception:
            pass
        return None

    @staticmethod
    def _screen_dimensions() -> tuple[int, int]:
        """Fetch primary screen dimensions in logical points via AppleScript.

        Uses Finder's desktop window bounds, which returns the main display's
        logical dimensions (not physical pixels). This is used to compute the
        Retina scale factor: scale = screenshot_pixels / logical_points.
        """
        # Method 1: Finder desktop bounds (most reliable)
        script = """
        tell application "Finder"
            set screenBounds to bounds of window of desktop
            return item 3 of screenBounds & "," & item 4 of screenBounds
        end tell
        """
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3.0,
            )
            res = proc.stdout.strip()
            if res:
                parts = [int(p) for p in res.split(",")]
                if len(parts) >= 2 and parts[0] > 0 and parts[1] > 0:
                    return parts[0], parts[1]
        except Exception:
            pass

        # Method 2: NSScreen via Python (if PyObjC available)
        try:
            import Quartz
            main_screen = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
            return int(main_screen.size.width), int(main_screen.size.height)
        except Exception:
            pass

        # Fallback: standard MacBook Air
        return 1440, 900

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return str(path)
