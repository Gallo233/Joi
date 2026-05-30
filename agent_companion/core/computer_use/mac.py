"""macOS Computer Use Backend.

Uses CoreGraphics for mouse/keyboard events, AppleScript for hotkeys,
and pbcopy/pbpaste for clipboard.  Thread-safe: no PySide6/Qt dependencies.
"""
from __future__ import annotations

import ctypes
from collections.abc import Sequence
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from agent_companion.core.computer_use.schemas import ComputerAction, ComputerObservation, ComputerUseResult
from agent_companion.core.windows_focus_mac import hide_foreground_companion_window, restore_window, run_applescript

if TYPE_CHECKING:
    from agent_companion.core.vision import VisionObserver


# ---------------------------------------------------------------------------
# CoreGraphics ctypes structures
# ---------------------------------------------------------------------------

class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


# CGMouseButton enum
_kCGMouseButtonLeft = 0
_kCGMouseButtonRight = 1
_kCGMouseButtonCenter = 2

# CGEventType enum
_kCGEventLeftMouseDown = 1
_kCGEventLeftMouseUp = 2
_kCGEventRightMouseDown = 3
_kCGEventRightMouseUp = 4
_kCGEventMouseMoved = 5
_kCGEventOtherMouseDown = 25
_kCGEventOtherMouseUp = 26

# kCGHIDEventTap = 0 for CGEventPost
_kCGHIDEventTap = 0


def _load_cg() -> ctypes.CDLL:
    """Load CoreGraphics framework."""
    return ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")


def _load_cf() -> ctypes.CDLL:
    """Load CoreFoundation framework."""
    return ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")


class MacComputerUseBackend:
    def __init__(self, workspace: Path, observer: "VisionObserver | None" = None) -> None:
        self.workspace = workspace.resolve()
        if observer is None:
            from agent_companion.core.vision.mac import MacScreenObserver
            self.observer = MacScreenObserver(workspace)
        else:
            self.observer = observer

    def observe(self, target: str = "active_window", query: str = "") -> ComputerObservation:
        return ComputerObservation.from_vision(self.observer.observe(target=target, query=query))

    def perform(self, action: ComputerAction) -> ComputerUseResult:
        if sys.platform != "darwin":
            return ComputerUseResult(False, action=action, error="computer use actions are currently implemented for macOS only")

        # Hide the Joi shell temporarily to ensure mouse click maps to the real window
        hidden_hwnd = hide_foreground_companion_window()
        try:
            return self._perform_unwrapped(action)
        except Exception as exc:
            return ComputerUseResult(False, action=action, error=f"{type(exc).__name__}: {exc}")
        finally:
            restore_window(hidden_hwnd)

    def perform_sequence(self, actions: Sequence[ComputerAction], settle_ms: int = 220) -> ComputerUseResult:
        if sys.platform != "darwin":
            return ComputerUseResult(False, action=ComputerAction("workflow"), error="computer use actions are currently implemented for macOS only")
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
        if action.action_type == "double_click":
            return self._double_click(action)
        if action.action_type == "drag":
            return self._drag(action)
        if action.action_type == "type_text":
            return self._type_text(action)
        if action.action_type == "scroll":
            return self._scroll(action)
        if action.action_type == "hotkey":
            return self._hotkey(action)
        if action.action_type == "open_app":
            return self._open_app(action)
        if action.action_type == "wait":
            time.sleep(max(0, int(action.delta or 0)) / 1000.0)
            return ComputerUseResult(True, action=action, summary="等待界面响应。")
        return ComputerUseResult(False, action=action, error=f"unsupported action: {action.action_type}")

    # -------------------------------------------------------------------
    # Mouse actions
    # -------------------------------------------------------------------

    def _click(self, action: ComputerAction) -> ComputerUseResult:
        if action.x is None or action.y is None:
            return ComputerUseResult(False, action=action, error="click requires x and y")

        x, y = int(action.x), int(action.y)

        # 1. Smooth cursor glide animation
        self._glide_mouse_pointer(x, y)

        # 2. Sync physical cursor position via MouseMoved event
        self._post_cg_event(x, y, _kCGEventMouseMoved, _kCGMouseButtonLeft)
        time.sleep(0.02)

        # 3. CGEvent click dispatch
        down_type, up_type, button_id = self._mouse_event_types(action.button)
        self._post_cg_event(x, y, down_type, button_id)
        time.sleep(0.05)
        self._post_cg_event(x, y, up_type, button_id)

        return ComputerUseResult(True, action=action, summary="点击了指定位置。")

    def _double_click(self, action: ComputerAction) -> ComputerUseResult:
        if action.x is None or action.y is None:
            return ComputerUseResult(False, action=action, error="double_click requires x and y")

        x, y = int(action.x), int(action.y)
        self._glide_mouse_pointer(x, y)
        self._post_cg_event(x, y, _kCGEventMouseMoved, _kCGMouseButtonLeft)
        time.sleep(0.02)

        down_type, up_type, button_id = self._mouse_event_types(action.button)

        # Two rapid clicks with kCGEventMouseMoved between them for proper double-click detection
        for _ in range(2):
            self._post_cg_event(x, y, down_type, button_id)
            time.sleep(0.03)
            self._post_cg_event(x, y, up_type, button_id)
            time.sleep(0.03)

        return ComputerUseResult(True, action=action, summary="双击了指定位置。")

    def _drag(self, action: ComputerAction) -> ComputerUseResult:
        """Drag from (x, y) to (end_x, end_y)."""
        if action.x is None or action.y is None:
            return ComputerUseResult(False, action=action, error="drag requires x, y")
        if action.end_x is None or action.end_y is None:
            return ComputerUseResult(False, action=action, error="drag requires end_x and end_y")

        sx, sy = int(action.x), int(action.y)
        ex, ey = int(action.end_x), int(action.end_y)

        # Move to start position
        self._glide_mouse_pointer(sx, sy)
        self._post_cg_event(sx, sy, _kCGEventMouseMoved, _kCGMouseButtonLeft)
        time.sleep(0.02)

        # Mouse down at start
        down_type, _, button_id = self._mouse_event_types(action.button)
        self._post_cg_event(sx, sy, down_type, button_id)
        time.sleep(0.05)

        # Smooth glide to end position
        self._glide_mouse_pointer(ex, ey)
        self._post_cg_event(ex, ey, _kCGEventMouseMoved, button_id)
        time.sleep(0.02)

        # Mouse up at end
        _, up_type, _ = self._mouse_event_types(action.button)
        self._post_cg_event(ex, ey, up_type, button_id)

        return ComputerUseResult(True, action=action, summary="完成了拖拽操作。")

    # -------------------------------------------------------------------
    # Text input (clipboard paste, IME-safe)
    # -------------------------------------------------------------------

    def _type_text(self, action: ComputerAction) -> ComputerUseResult:
        if not action.text:
            return ComputerUseResult(False, action=action, error="type_text requires text")

        # Save clipboard, set text, paste, restore
        previous = self._clipboard_text()
        self._set_clipboard_text(action.text)

        # Cmd+V via CGEvent (faster and more reliable than AppleScript for paste)
        self._cg_hotkey("v", ["cmd"])
        time.sleep(0.08)

        if previous is not None:
            self._set_clipboard_text(previous)
        return ComputerUseResult(True, action=action, summary="输入了一段文字。")

    # -------------------------------------------------------------------
    # Scroll
    # -------------------------------------------------------------------

    def _scroll(self, action: ComputerAction) -> ComputerUseResult:
        delta = int(action.delta or -3)
        try:
            cg = _load_cg()
            cg.CGEventCreateScrollWheelEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32]
            cg.CGEventCreateScrollWheelEvent.restype = ctypes.c_void_p
            cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]

            cf = _load_cf()
            cf.CFRelease.argtypes = [ctypes.c_void_p]

            # units=1 (lines), wheelCount=1
            event = cg.CGEventCreateScrollWheelEvent(None, 1, 1, delta)
            if event:
                cg.CGEventPost(0, event)
                cf.CFRelease(event)
        except Exception as exc:
            return ComputerUseResult(False, action=action, error=f"scroll_failed: {exc}")

        return ComputerUseResult(True, action=action, summary="滚动了当前画面。")

    # -------------------------------------------------------------------
    # Hotkey (AppleScript for System Events keystroke, CGEvent for simple combos)
    # -------------------------------------------------------------------

    def _hotkey(self, action: ComputerAction) -> ComputerUseResult:
        if not action.keys:
            return ComputerUseResult(False, action=action, error="hotkey requires keys")

        # Normalize keys
        modifiers = []
        key_char = None
        for key in action.keys:
            folded = key.strip().casefold()
            if folded in {"ctrl", "control"}:
                modifiers.append("control")
            elif folded in {"alt", "option"}:
                modifiers.append("option")
            elif folded in {"shift"}:
                modifiers.append("shift")
            elif folded in {"cmd", "command", "win", "meta"}:
                modifiers.append("command")
            else:
                key_char = key

        if key_char:
            self._cg_hotkey(key_char, modifiers)
        else:
            # Modifiers only - hold briefly
            return ComputerUseResult(True, action=action, summary="按下了修饰键。")

        return ComputerUseResult(True, action=action, summary="按下了快捷键。")

    def _cg_hotkey(self, key_char: str, modifiers: list[str]) -> None:
        """Dispatch a hotkey using AppleScript System Events (handles all key types)."""
        modifier_str = ", ".join(f"{m} down" for m in modifiers)
        using_clause = f" using {{{modifier_str}}}" if modifiers else ""

        folded_char = key_char.strip().casefold()
        special_keys = {
            "enter": "return", "return": "return", "tab": "tab",
        }
        special_key_codes = {
            "esc": 53, "escape": 53,
            "backspace": 51, "delete": 117,
            "left": 123, "right": 124, "up": 125, "down": 126,
            "space": 49,
            "f1": 122, "f2": 120, "f3": 99, "f4": 118,
            "f5": 96, "f6": 97, "f7": 98, "f8": 100,
            "f9": 101, "f10": 109, "f11": 103, "f12": 111,
        }

        if folded_char in special_keys:
            script = f'tell application "System Events" to keystroke {special_keys[folded_char]}{using_clause}'
        elif folded_char in special_key_codes:
            script = f'tell application "System Events" to key code {special_key_codes[folded_char]}{using_clause}'
        else:
            escaped = key_char.replace("\\", "\\\\").replace('"', '\\"')
            script = f'tell application "System Events" to keystroke "{escaped}"{using_clause}'

        run_applescript(script)

    # -------------------------------------------------------------------
    # App launching (Spotlight + open -a fallback)
    # -------------------------------------------------------------------

    def _open_app(self, action: ComputerAction) -> ComputerUseResult:
        """Open an application by name. Tries Spotlight first, then `open -a`."""
        app_name = (action.app_name or action.text or "").strip()
        if not app_name:
            return ComputerUseResult(False, action=action, error="open_app requires app name")

        # Strategy 1: Spotlight (cmd+space, type name, enter)
        spotlight_ok = self._try_spotlight_launch(app_name)

        if spotlight_ok:
            return ComputerUseResult(True, action=action, summary=f"通过 Spotlight 打开了 {app_name}。")

        # Strategy 2: open -a (always works for known apps)
        open_a_ok = self._try_open_a_launch(app_name)
        if open_a_ok:
            return ComputerUseResult(True, action=action, summary=f"打开了 {app_name}。")

        return ComputerUseResult(False, action=action, error=f"无法打开 {app_name}，请检查应用名称是否正确。")

    def _try_spotlight_launch(self, app_name: str) -> bool:
        """Try launching via Spotlight. Returns True if the sequence completed without error."""
        try:
            # Cmd+Space
            self._cg_hotkey("space", ["cmd"])
            time.sleep(0.3)

            # Type app name
            previous = self._clipboard_text()
            self._set_clipboard_text(app_name)
            self._cg_hotkey("v", ["cmd"])
            time.sleep(0.5)

            if previous is not None:
                self._set_clipboard_text(previous)

            # Enter
            self._cg_hotkey("return", [])
            time.sleep(0.5)
            return True
        except Exception:
            return False

    def _try_open_a_launch(self, app_name: str) -> bool:
        """Try launching via `open -a`."""
        try:
            proc = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True, timeout=5.0,
            )
            return proc.returncode == 0
        except Exception:
            return False

    # -------------------------------------------------------------------
    # Mouse cursor animation
    # -------------------------------------------------------------------

    def _glide_mouse_pointer(self, target_x: int, target_y: int) -> None:
        """Smooth ease-out cursor glide using CoreGraphics CGWarpMouseCursorPosition."""
        try:
            cg = _load_cg()
            cg.CGEventCreate.argtypes = [ctypes.c_void_p]
            cg.CGEventCreate.restype = ctypes.c_void_p
            cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
            cg.CGEventGetLocation.restype = CGPoint
            cg.CGWarpMouseCursorPosition.argtypes = [CGPoint]

            cf = _load_cf()
            cf.CFRelease.argtypes = [ctypes.c_void_p]

            # Get current mouse position
            event = cg.CGEventCreate(None)
            if not event:
                self._warp_cursor(target_x, target_y)
                return
            point = cg.CGEventGetLocation(event)
            x1, y1 = point.x, point.y
            cf.CFRelease(event)
        except Exception:
            self._warp_cursor(target_x, target_y)
            return

        # Smooth ease-out interpolation (15 frames, ~90ms total)
        steps = 15
        for i in range(1, steps + 1):
            t = i / steps
            t_smooth = 1.0 - (1.0 - t) ** 3  # cubic ease-out
            curr_x = x1 + (target_x - x1) * t_smooth
            curr_y = y1 + (target_y - y1) * t_smooth
            try:
                cg.CGWarpMouseCursorPosition(CGPoint(float(curr_x), float(curr_y)))
            except Exception:
                pass
            time.sleep(0.006)

    @staticmethod
    def _warp_cursor(x: int, y: int) -> None:
        """Direct cursor warp (no animation)."""
        try:
            cg = _load_cg()
            cg.CGWarpMouseCursorPosition.argtypes = [CGPoint]
            cg.CGWarpMouseCursorPosition(CGPoint(float(x), float(y)))
        except Exception:
            pass

    # -------------------------------------------------------------------
    # CGEvent mouse dispatch
    # -------------------------------------------------------------------

    @staticmethod
    def _post_cg_event(x: int, y: int, event_type: int, button_id: int) -> None:
        """Post a CoreGraphics mouse event."""
        try:
            cg = _load_cg()
            cg.CGEventCreateMouseEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32
            ]
            cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
            cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]

            cf = _load_cf()
            cf.CFRelease.argtypes = [ctypes.c_void_p]

            point = CGPoint(float(x), float(y))
            event = cg.CGEventCreateMouseEvent(None, event_type, point, button_id)
            if event:
                cg.CGEventPost(_kCGHIDEventTap, event)
                cf.CFRelease(event)
        except Exception:
            pass

    @staticmethod
    def _mouse_event_types(button: str) -> tuple[int, int, int]:
        """Convert button label to CGEvent types (down, up, button_id)."""
        folded = (button or "left").casefold()
        if folded == "right":
            return _kCGEventRightMouseDown, _kCGEventRightMouseUp, _kCGMouseButtonRight
        if folded == "middle":
            return _kCGEventOtherMouseDown, _kCGEventOtherMouseUp, _kCGMouseButtonCenter
        return _kCGEventLeftMouseDown, _kCGEventLeftMouseUp, _kCGMouseButtonLeft

    # -------------------------------------------------------------------
    # Clipboard (thread-safe, no Qt)
    # -------------------------------------------------------------------

    @staticmethod
    def _clipboard_text() -> str | None:
        try:
            proc = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=1.0)
            if proc.returncode == 0:
                return proc.stdout
        except Exception:
            pass
        return None

    @staticmethod
    def _set_clipboard_text(text: str) -> None:
        try:
            subprocess.run(["pbcopy"], input=text, text=True, timeout=1.0)
        except Exception:
            pass
