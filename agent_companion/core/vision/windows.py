from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
import sys

from agent_companion.core.vision.schemas import CaptureRect, VisionObservation


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

        hwnd = self._foreground_window() if target == "active_window" else 0
        title = self._window_title(hwnd) if hwnd else ""
        screen_rect = self._window_rect(hwnd) if hwnd else self._screen_rect(screen)
        pixmap = screen.grabWindow(hwnd)
        if pixmap.isNull():
            raise RuntimeError("screen capture returned an empty image")
        capture_rect = self._capture_rect(screen_rect, pixmap.width(), pixmap.height())

        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"{target}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
        if not pixmap.save(str(output_path), "PNG"):
            raise RuntimeError("failed to save screen capture")

        return VisionObservation(
            target=target,
            screenshot_path=output_path,
            screenshot_rel=self._rel(output_path),
            width=pixmap.width(),
            height=pixmap.height(),
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
    def _foreground_window() -> int:
        return int(ctypes.windll.user32.GetForegroundWindow())

    @staticmethod
    def _window_title(hwnd: int) -> str:
        if not hwnd:
            return ""
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

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
