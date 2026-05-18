from __future__ import annotations

import ctypes
from pathlib import Path
import sys

from agent_companion.core.computer_use.schemas import ComputerAction, ComputerObservation, ComputerUseResult
from agent_companion.core.vision import VisionObserver, WindowsScreenObserver


class WindowsComputerUseBackend:
    def __init__(self, workspace: Path, observer: VisionObserver | None = None) -> None:
        self.workspace = workspace.resolve()
        self.observer = observer or WindowsScreenObserver(workspace)

    def observe(self, target: str = "active_window", query: str = "") -> ComputerObservation:
        return ComputerObservation.from_vision(self.observer.observe(target=target, query=query))

    def perform(self, action: ComputerAction) -> ComputerUseResult:
        if sys.platform != "win32":
            return ComputerUseResult(False, action=action, error="computer use actions are currently implemented for Windows only")
        try:
            if action.action_type == "click":
                return self._click(action)
            if action.action_type == "type_text":
                return self._type_text(action)
            if action.action_type == "scroll":
                return self._scroll(action)
            if action.action_type == "hotkey":
                return self._hotkey(action)
            return ComputerUseResult(False, action=action, error=f"unsupported action: {action.action_type}")
        except Exception as exc:
            return ComputerUseResult(False, action=action, error=f"{type(exc).__name__}: {exc}")

    def _click(self, action: ComputerAction) -> ComputerUseResult:
        if action.x is None or action.y is None:
            return ComputerUseResult(False, action=action, error="click requires x and y")
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(action.x), int(action.y))
        down, up = self._mouse_flags(action.button)
        user32.mouse_event(down, 0, 0, 0, 0)
        user32.mouse_event(up, 0, 0, 0, 0)
        return ComputerUseResult(True, action=action, summary="点击了指定位置。")

    def _type_text(self, action: ComputerAction) -> ComputerUseResult:
        if not action.text:
            return ComputerUseResult(False, action=action, error="type_text requires text")
        user32 = ctypes.windll.user32
        for char in action.text:
            code = ord(char)
            user32.keybd_event(0, code, 0x0004, 0)
            user32.keybd_event(0, code, 0x0004 | 0x0002, 0)
        return ComputerUseResult(True, action=action, summary="输入了一段文字。")

    def _scroll(self, action: ComputerAction) -> ComputerUseResult:
        delta = int(action.delta or -3)
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, delta * 120, 0)
        return ComputerUseResult(True, action=action, summary="滚动了当前画面。")

    def _hotkey(self, action: ComputerAction) -> ComputerUseResult:
        if not action.keys:
            return ComputerUseResult(False, action=action, error="hotkey requires keys")
        user32 = ctypes.windll.user32
        codes = [self._vk_code(key) for key in action.keys]
        if any(code is None for code in codes):
            return ComputerUseResult(False, action=action, error="unsupported hotkey key")
        for code in codes:
            user32.keybd_event(int(code), 0, 0, 0)
        for code in reversed(codes):
            user32.keybd_event(int(code), 0, 0x0002, 0)
        return ComputerUseResult(True, action=action, summary="按下了快捷键。")

    @staticmethod
    def _mouse_flags(button: str) -> tuple[int, int]:
        value = (button or "left").casefold()
        if value == "right":
            return 0x0008, 0x0010
        if value == "middle":
            return 0x0020, 0x0040
        return 0x0002, 0x0004

    @staticmethod
    def _vk_code(key: str) -> int | None:
        normalized = key.strip().casefold()
        aliases = {
            "ctrl": 0x11,
            "control": 0x11,
            "alt": 0x12,
            "shift": 0x10,
            "win": 0x5B,
            "meta": 0x5B,
            "cmd": 0x5B,
            "command": 0x5B,
            "enter": 0x0D,
            "return": 0x0D,
            "tab": 0x09,
            "esc": 0x1B,
            "escape": 0x1B,
            "space": 0x20,
            "backspace": 0x08,
            "delete": 0x2E,
            "del": 0x2E,
            "home": 0x24,
            "end": 0x23,
            "pageup": 0x21,
            "pagedown": 0x22,
            "left": 0x25,
            "up": 0x26,
            "right": 0x27,
            "down": 0x28,
        }
        if normalized in aliases:
            return aliases[normalized]
        if len(normalized) == 1 and normalized.isalnum():
            return ord(normalized.upper())
        if normalized.startswith("f") and normalized[1:].isdigit():
            number = int(normalized[1:])
            if 1 <= number <= 24:
                return 0x70 + number - 1
        return None
