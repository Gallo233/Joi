from __future__ import annotations

import ctypes
import time
from ctypes import wintypes


COMPANION_WINDOW_TITLES = {"joi", "joi desktop", "shinsekai mvp", "agent companion"}


def foreground_window() -> int:
    return int(ctypes.windll.user32.GetForegroundWindow())


def root_window(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        return int(ctypes.windll.user32.GetAncestor(wintypes.HWND(hwnd), 2))
    except Exception:
        return int(hwnd)


def window_from_point(x: int, y: int) -> int:
    try:
        point = wintypes.POINT(int(x), int(y))
        return root_window(int(ctypes.windll.user32.WindowFromPoint(point)))
    except Exception:
        return 0


def window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def is_companion_window(hwnd: int) -> bool:
    title = window_title(hwnd).strip().casefold()
    return title in COMPANION_WINDOW_TITLES


def is_visible_window(hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        return bool(ctypes.windll.user32.IsWindowVisible(wintypes.HWND(hwnd)))
    except Exception:
        return False


def hide_foreground_companion_window(settle_seconds: float = 0.18) -> int | None:
    hwnd = foreground_window()
    if not hwnd or not is_companion_window(hwnd):
        return None
    ctypes.windll.user32.ShowWindow(wintypes.HWND(hwnd), 0)
    time.sleep(max(0.02, float(settle_seconds or 0.18)))
    return hwnd


def restore_window(hwnd: int | None, settle_seconds: float = 0.08) -> None:
    if not hwnd:
        return
    try:
        ctypes.windll.user32.ShowWindow(wintypes.HWND(hwnd), 9)
        ctypes.windll.user32.SetForegroundWindow(wintypes.HWND(hwnd))
        time.sleep(max(0.02, float(settle_seconds or 0.08)))
    except Exception:
        return
