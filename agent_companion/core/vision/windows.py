from __future__ import annotations

import ctypes
from datetime import datetime
from pathlib import Path
import sys

from agent_companion.core.vision.schemas import VisionObservation


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
        pixmap = screen.grabWindow(hwnd)
        if pixmap.isNull():
            raise RuntimeError("screen capture returned an empty image")

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

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return str(path)
