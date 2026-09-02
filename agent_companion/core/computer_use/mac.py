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
        restore_foreground = action.action_type not in {"open_app", "open_url"}
        hidden_hwnd = hide_foreground_companion_window()
        try:
            return self._perform_unwrapped(action)
        except Exception as exc:
            return ComputerUseResult(False, action=action, error=f"{type(exc).__name__}: {exc}")
        finally:
            restore_window(hidden_hwnd, activate=restore_foreground)

    def perform_sequence(self, actions: Sequence[ComputerAction], settle_ms: int = 220) -> ComputerUseResult:
        if sys.platform != "darwin":
            return ComputerUseResult(False, action=ComputerAction("workflow"), error="computer use actions are currently implemented for macOS only")
        restore_foreground = not any(action.action_type in {"open_app", "open_url"} for action in actions)
        hidden_hwnd = hide_foreground_companion_window()
        try:
            last_result: ComputerUseResult | None = None
            for action in actions:
                if action.action_type == "wait":
                    time.sleep(max(0, int(action.delta or settle_ms)) / 1000.0)
                    continue
                result = self._perform_unwrapped(action)
                if not result.ok:
                    return ComputerUseResult(False, action=ComputerAction("workflow"), error=result.error or "workflow step failed")
                last_result = result
                time.sleep(max(0, int(settle_ms or 0)) / 1000.0)
            return ComputerUseResult(True, action=ComputerAction("workflow"), summary=last_result.summary if last_result else "完成了多步电脑操作。")
        except Exception as exc:
            return ComputerUseResult(False, action=ComputerAction("workflow"), error=f"{type(exc).__name__}: {exc}")
        finally:
            restore_window(hidden_hwnd, activate=restore_foreground)

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
        if action.action_type == "open_url":
            return self._open_url(action)
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

        previous = self._clipboard_text()
        try:
            if not self._set_clipboard_text(action.text):
                return ComputerUseResult(False, action=action, error="type_text clipboard set failed")
            if self._clipboard_text() != action.text:
                if not self._set_clipboard_text(action.text) or self._clipboard_text() != action.text:
                    return ComputerUseResult(False, action=action, error="type_text clipboard verification failed")

            if not self._cg_hotkey("v", ["cmd"]):
                return ComputerUseResult(False, action=action, error="type_text paste hotkey failed")
            time.sleep(0.08)
            return ComputerUseResult(True, action=action, summary="输入了一段文字。")
        finally:
            restore_text = previous if previous is not None else ""
            self._set_clipboard_text(restore_text)

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
            if not self._cg_hotkey(key_char, modifiers):
                return ComputerUseResult(False, action=action, error="hotkey dispatch failed")
        else:
            # Modifiers only - hold briefly
            return ComputerUseResult(True, action=action, summary="按下了修饰键。")

        return ComputerUseResult(True, action=action, summary="按下了快捷键。")

    def _cg_hotkey(self, key_char: str, modifiers: list[str]) -> bool:
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

        return _run_applescript_ok(script)

    # -------------------------------------------------------------------
    # App launching (Spotlight + open -a fallback)
    # -------------------------------------------------------------------

    def _open_app(self, action: ComputerAction) -> ComputerUseResult:
        """Open an application by name and verify it actually launched."""
        raw_app_name = (action.app_name or action.text or "").strip()
        app_name = _canonical_app_name(raw_app_name)
        if not app_name:
            return ComputerUseResult(False, action=action, error="open_app requires app name")

        errors: list[str] = []
        if self._try_open_a_launch(app_name, errors=errors) and self._wait_for_app_launch(app_name):
            return ComputerUseResult(True, action=action, summary=f"打开了 {app_name}。")

        app_path = _resolve_app_path(app_name)
        if app_path and self._try_open_path_launch(app_path, errors=errors) and self._wait_for_app_launch(app_name):
            return ComputerUseResult(True, action=action, summary=f"打开了 {app_name}。")

        if self._try_applescript_activate(app_name, errors=errors) and self._wait_for_app_launch(app_name):
            return ComputerUseResult(True, action=action, summary=f"打开了 {app_name}。")

        if self._try_spotlight_launch(app_name) and self._wait_for_app_launch(app_name):
            return ComputerUseResult(True, action=action, summary=f"通过 Spotlight 打开了 {app_name}。")

        detail = "; ".join(item for item in errors if item)[:240]
        suffix = f" ({detail})" if detail else ""
        return ComputerUseResult(False, action=action, error=f"无法确认 {app_name} 已打开{suffix}")

    def _open_url(self, action: ComputerAction) -> ComputerUseResult:
        """Open a URL in a desktop browser using macOS Launch Services."""
        url = (action.text or "").strip()
        if not url:
            return ComputerUseResult(False, action=action, error="open_url requires url")
        browser = _canonical_app_name(action.app_name or "")
        command = ["open", url] if not browser or browser == "Default" else ["open", "-a", browser, url]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=5.0)
        except Exception as exc:
            return ComputerUseResult(False, action=action, error=f"open_url_failed: {type(exc).__name__}")
        if proc.returncode != 0:
            detail = _safe_process_error(proc.stderr or proc.stdout)
            return ComputerUseResult(False, action=action, error=f"open_url_failed: {detail}" if detail else "open_url_failed")
        if browser and not self._wait_for_app_launch(browser, timeout=6.0):
            return ComputerUseResult(False, action=action, error=f"无法确认 {browser} 已打开")
        return ComputerUseResult(True, action=action, summary="打开了网页。")

    def _try_spotlight_launch(self, app_name: str) -> bool:
        """Try launching via Spotlight. Returns True if the sequence completed without error."""
        try:
            # Cmd+Space
            if not self._cg_hotkey("space", ["cmd"]):
                return False
            time.sleep(0.3)

            # Type app name
            previous = self._clipboard_text()
            try:
                if not self._set_clipboard_text(app_name):
                    return False
                if not self._cg_hotkey("v", ["cmd"]):
                    return False
                time.sleep(0.5)
            finally:
                self._set_clipboard_text(previous if previous is not None else "")

            # Enter
            if not self._cg_hotkey("return", []):
                return False
            time.sleep(0.5)
            return True
        except Exception:
            return False

    def _try_open_a_launch(self, app_name: str, *, errors: list[str] | None = None) -> bool:
        """Try launching via `open -a`."""
        try:
            proc = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True, text=True, timeout=5.0,
            )
            if proc.returncode != 0 and errors is not None:
                errors.append(_safe_process_error(proc.stderr or proc.stdout))
            return proc.returncode == 0
        except Exception as exc:
            if errors is not None:
                errors.append(type(exc).__name__)
            return False

    def _try_open_path_launch(self, app_path: Path, *, errors: list[str] | None = None) -> bool:
        """Try launching a concrete .app bundle path. Do not use `open -a` for paths."""
        try:
            proc = subprocess.run(
                ["open", str(app_path)],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            if proc.returncode != 0 and errors is not None:
                errors.append(_safe_process_error(proc.stderr or proc.stdout))
            return proc.returncode == 0
        except Exception as exc:
            if errors is not None:
                errors.append(type(exc).__name__)
            return False

    def _try_applescript_activate(self, app_name: str, *, errors: list[str] | None = None) -> bool:
        escaped = app_name.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "{escaped}" to activate'
        try:
            proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5.0)
            if proc.returncode == 0:
                return True
            if errors is not None:
                errors.append(_safe_process_error(proc.stderr or proc.stdout) or "osascript_activate_failed")
        except Exception as exc:
            if errors is not None:
                errors.append(type(exc).__name__)
            return False
        return False

    def _wait_for_app_launch(self, app_name: str, timeout: float = 6.0) -> bool:
        deadline = time.time() + max(0.5, float(timeout or 6.0))
        while time.time() < deadline:
            frontmost = self._frontmost_app_name()
            if _same_app_name(frontmost, app_name):
                return True
            if not frontmost and self._is_app_running(app_name):
                return True
            time.sleep(0.25)
        return False

    def _frontmost_app_name(self) -> str:
        script = """
        tell application "System Events"
            try
                return name of first application process whose frontmost is true
            on error
                return ""
            end try
        end tell
        """
        return run_applescript(script).strip()

    def _is_app_running(self, app_name: str) -> bool:
        escaped = app_name.replace("\\", "\\\\").replace('"', '\\"')
        script = f"""
        tell application "System Events"
            try
                return exists application process "{escaped}"
            on error
                return false
            end try
        end tell
        """
        if run_applescript(script).strip().casefold() == "true":
            return True
        try:
            proc = subprocess.run(["pgrep", "-x", app_name], capture_output=True, text=True, timeout=2.0)
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
    def _set_clipboard_text(text: str) -> bool:
        try:
            proc = subprocess.run(["pbcopy"], input=text, text=True, timeout=1.0)
            return proc.returncode == 0
        except Exception:
            return False


def _canonical_app_name(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    aliases = {
        "safari": "Safari",
        "safari.app": "Safari",
        "edge": "Microsoft Edge",
        "microsoft edge": "Microsoft Edge",
        "chrome": "Google Chrome",
        "google chrome": "Google Chrome",
        "codex": "Codex",
        "terminal": "Terminal",
        "finder": "Finder",
    }
    return aliases.get(text.casefold(), text)


def _resolve_app_path(app_name: str) -> Path | None:
    bundle = app_name if app_name.casefold().endswith(".app") else f"{app_name}.app"
    candidates = (
        Path("/Applications") / bundle,
        Path("/System/Applications") / bundle,
        Path("/System/Cryptexes/App/System/Applications") / bundle,
        Path.home() / "Applications" / bundle,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _same_app_name(left: str, right: str) -> bool:
    a = _canonical_app_name(left).casefold().replace(".app", "").strip()
    b = _canonical_app_name(right).casefold().replace(".app", "").strip()
    return bool(a and b and a == b)


def _safe_process_error(value: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    text = text.replace(str(Path.home()), "~")
    return text[:160]


def _run_applescript_ok(script: str) -> bool:
    try:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=3.0)
        return proc.returncode == 0
    except Exception:
        return False
