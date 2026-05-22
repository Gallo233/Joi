from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
import sys

from agent_companion.core.vision.schemas import CaptureRect, VisionObservation
from agent_companion.core.windows_focus import foreground_window, hide_foreground_companion_window, is_companion_window, is_visible_window, restore_window, window_from_point, window_title


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class WindowsScreenObserver:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.output_dir = self.workspace / "data" / "agent_companion" / "vision"

    def observe(self, target: str = "active_window", query: str = "") -> VisionObservation:
        target = self._normalize_target(target)
        if sys.platform != "win32":
            raise RuntimeError("screen observation is currently implemented for Windows only")

        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication

        app = QGuiApplication.instance() or QApplication([])
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            raise RuntimeError("no primary screen is available")

        original_hwnd = foreground_window() if target == "active_window" else 0
        original_rect = self._window_rect(original_hwnd) if original_hwnd else None
        hidden_hwnd = hide_foreground_companion_window(settle_seconds=0.28)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"{target}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
        try:
            hwnd = self._capture_window_handle(target, hidden_hwnd, original_rect)
            title = window_title(hwnd) if hwnd else ""
            screen_rect = self._window_rect(hwnd) if hwnd else self._screen_rect(screen)
            width, height = self._save_screen_capture(output_path, screen_rect, screen)
            capture_rect = self._capture_rect(screen_rect, width, height)
        finally:
            restore_window(hidden_hwnd)

        return VisionObservation(
            target=target,
            screenshot_path=output_path,
            screenshot_rel=self._rel(output_path),
            width=width,
            height=height,
            title=title,
            window_handle=hwnd or None,
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
    def _capture_window_handle(target: str, hidden_hwnd: int | None, original_rect: tuple[int, int, int, int] | None) -> int:
        if target != "active_window":
            return 0
        if hidden_hwnd and original_rect is not None:
            left, top, width, height = original_rect
            hwnd = window_from_point(left + max(1, width // 2), top + max(1, height // 2))
            if hwnd and not is_companion_window(hwnd) and is_visible_window(hwnd):
                return hwnd
        hwnd = foreground_window()
        if hwnd and not is_companion_window(hwnd) and is_visible_window(hwnd):
            return hwnd
        return 0

    @staticmethod
    def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
        if not hwnd:
            return None
        rect = _RECT()
        try:
            # DWMWA_EXTENDED_FRAME_BOUNDS gives the visually captured window bounds,
            # which better matches Qt's active-window screenshot than GetWindowRect.
            result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint(9),
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if result == 0 and rect.right > rect.left and rect.bottom > rect.top:
                return (int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top))
        except Exception:
            pass
        try:
            if ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                if rect.right > rect.left and rect.bottom > rect.top:
                    return (int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top))
        except Exception:
            return None
        return None

    @staticmethod
    def _save_screen_capture(output_path: Path, rect: tuple[int, int, int, int] | None, screen: object) -> tuple[int, int]:
        try:
            from PIL import ImageGrab

            bbox = None
            if rect is not None:
                left, top, width, height = rect
                if width > 0 and height > 0:
                    bbox = (left, top, left + width, top + height)
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
            if image.width <= 0 or image.height <= 0:
                raise RuntimeError("empty Pillow capture")
            image.save(output_path, "PNG")
            return int(image.width), int(image.height)
        except Exception:
            hwnd = 0
            if rect is not None:
                hwnd = foreground_window()
            pixmap = screen.grabWindow(hwnd)
            if pixmap.isNull():
                raise RuntimeError("screen capture returned an empty image")
            if not pixmap.save(str(output_path), "PNG"):
                raise RuntimeError("failed to save screen capture")
            return int(pixmap.width()), int(pixmap.height())

    @staticmethod
    def _screen_rect(screen: object) -> tuple[int, int, int, int] | None:
        try:
            geometry = screen.geometry()
            return (int(geometry.x()), int(geometry.y()), int(geometry.width()), int(geometry.height()))
        except Exception:
            return None

    @staticmethod
    def _capture_rect(rect: tuple[int, int, int, int] | None, capture_width: int, capture_height: int) -> CaptureRect | None:
        if rect is None:
            return None
        screen_x, screen_y, width, height = rect
        if width <= 0 or height <= 0 or capture_width <= 0 or capture_height <= 0:
            return None
        scale_x = capture_width / width
        scale_y = capture_height / height
        capture_scale = (scale_x + scale_y) / 2
        return CaptureRect(
            screen_x=screen_x,
            screen_y=screen_y,
            width=width,
            height=height,
            capture_scale=capture_scale,
            scale_x=scale_x,
            scale_y=scale_y,
        )

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return str(path)
