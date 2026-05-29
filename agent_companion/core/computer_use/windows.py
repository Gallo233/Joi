from __future__ import annotations

import ctypes
from collections.abc import Sequence
from pathlib import Path
import sys
import time
from ctypes import wintypes

from agent_companion.core.computer_use.schemas import ComputerAction, ComputerObservation, ComputerUseResult
from agent_companion.core.vision import VisionObserver, WindowsScreenObserver
from agent_companion.core.windows_focus import hide_foreground_companion_window, restore_window


class WindowsComputerUseBackend:
    def __init__(self, workspace: Path, observer: VisionObserver | None = None) -> None:
        self.workspace = workspace.resolve()
        self.observer = observer or WindowsScreenObserver(workspace)

    def observe(self, target: str = "active_window", query: str = "") -> ComputerObservation:
        return ComputerObservation.from_vision(self.observer.observe(target=target, query=query))

    def perform(self, action: ComputerAction) -> ComputerUseResult:
        if sys.platform != "win32":
            return ComputerUseResult(False, action=action, error="computer use actions are currently implemented for Windows only")
        hidden_hwnd = hide_foreground_companion_window()
        try:
            return self._perform_unwrapped(action)
        except Exception as exc:
            return ComputerUseResult(False, action=action, error=f"{type(exc).__name__}: {exc}")
        finally:
            restore_window(hidden_hwnd)

    def perform_sequence(self, actions: Sequence[ComputerAction], settle_ms: int = 220) -> ComputerUseResult:
        if sys.platform != "win32":
            return ComputerUseResult(False, action=ComputerAction("workflow"), error="computer use actions are currently implemented for Windows only")
        hidden_hwnd = hide_foreground_companion_window()
        try:
            for action in actions:
                if action.action_type == "wait":
                    time.sleep(max(0, int(action.delta or settle_ms)) / 1000.0)
                    continue
                result = self._perform_unwrapped(action)
                if not result.ok:
                    return ComputerUseResult(False, action=ComputerAction("workflow"), error=result.error or "workflow step failed")
                time.sleep(max(0, int(settle_ms or 0)) / 1000.0)
            return ComputerUseResult(True, action=ComputerAction("workflow"), summary="完成了多步电脑操作。")
        except Exception as exc:
            return ComputerUseResult(False, action=ComputerAction("workflow"), error=f"{type(exc).__name__}: {exc}")
        finally:
            restore_window(hidden_hwnd)

    def _perform_unwrapped(self, action: ComputerAction) -> ComputerUseResult:
        if action.action_type == "click":
            return self._click(action)
        if action.action_type == "type_text":
            return self._type_text(action)
        if action.action_type == "scroll":
            return self._scroll(action)
        if action.action_type == "hotkey":
            return self._hotkey(action)
        if action.action_type == "wait":
            time.sleep(max(0, int(action.delta or 0)) / 1000.0)
            return ComputerUseResult(True, action=action, summary="等待界面响应。")
        return ComputerUseResult(False, action=action, error=f"unsupported action: {action.action_type}")

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
        previous = self._clipboard_text()
        self._set_clipboard_text(action.text)
        current = self._clipboard_text()
        if current != action.text:
            self._set_clipboard_text(action.text)
        self._hotkey(ComputerAction("hotkey", keys=("ctrl", "v")))
        time.sleep(0.45)
        if previous is not None:
            self._set_clipboard_text(previous)
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

    @staticmethod
    def _clipboard_text() -> str | None:
        if sys.platform != "win32":
            return None
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        cf_unicode_text = 13
        if not WindowsComputerUseBackend._open_clipboard_with_retry():
            return None
        try:
            if not user32.IsClipboardFormatAvailable(cf_unicode_text):
                return ""
            kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
            kernel32.GlobalLock.restype = wintypes.LPVOID
            kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
            kernel32.GlobalUnlock.restype = wintypes.BOOL
            user32.GetClipboardData.argtypes = (wintypes.UINT,)
            user32.GetClipboardData.restype = wintypes.HANDLE
            handle = user32.GetClipboardData(cf_unicode_text)
            if not handle:
                return ""
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    @staticmethod
    def _set_clipboard_text(text: str) -> None:
        if sys.platform != "win32":
            raise RuntimeError("native clipboard is currently implemented for Windows only")
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        cf_unicode_text = 13
        gmem_moveable = 0x0002
        encoded = (text + "\0").encode("utf-16-le")
        if not WindowsComputerUseBackend._open_clipboard_with_retry():
            raise RuntimeError("clipboard is busy")
        handle = None
        try:
            if not user32.EmptyClipboard():
                raise RuntimeError("failed to clear clipboard")
            kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
            kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
            kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
            kernel32.GlobalLock.restype = wintypes.LPVOID
            kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
            kernel32.GlobalUnlock.restype = wintypes.BOOL
            kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
            kernel32.GlobalFree.restype = wintypes.HGLOBAL
            user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HGLOBAL)
            user32.SetClipboardData.restype = wintypes.HANDLE
            handle = kernel32.GlobalAlloc(gmem_moveable, len(encoded))
            if not handle:
                raise RuntimeError("failed to allocate clipboard memory")
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                raise RuntimeError("failed to lock clipboard memory")
            try:
                ctypes.memmove(pointer, encoded, len(encoded))
            finally:
                kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(cf_unicode_text, handle):
                raise RuntimeError("failed to set clipboard data")
            handle = None
        finally:
            user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)

    @staticmethod
    def _open_clipboard_with_retry() -> bool:
        user32 = ctypes.windll.user32
        user32.OpenClipboard.argtypes = (wintypes.HWND,)
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = ()
        user32.CloseClipboard.restype = wintypes.BOOL
        for _ in range(12):
            if user32.OpenClipboard(None):
                return True
            time.sleep(0.025)
        return False
