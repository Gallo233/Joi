from __future__ import annotations

import subprocess
import time

COMPANION_PROCESS_NAMES = {"joi", "shinsekai mvp", "agent companion", "electron", "node"}


def run_applescript(script: str) -> str:
    """Execute an AppleScript snippet securely using osascript."""
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return ""


def foreground_window() -> int:
    """Get the active frontmost process PID on macOS."""
    script = """
    tell application "System Events"
        try
            set frontmostProcess to first process whose frontmost is true
            return unix id of frontmostProcess
        on error
            return 0
        end try
    end tell
    """
    res = run_applescript(script)
    try:
        return int(res) if res else 0
    except ValueError:
        return 0


def window_title(hwnd: int) -> str:
    """Get the window title of a process by its PID on macOS."""
    if not hwnd:
        return ""
    script = f"""
    tell application "System Events"
        try
            set targetProcess to first process whose unix id is {hwnd}
            if (count of windows of targetProcess) > 0 then
                return name of first window of targetProcess
            end if
        end try
    end tell
    return ""
    """
    return run_applescript(script)


def process_name(hwnd: int) -> str:
    """Get the name of a process by its PID on macOS."""
    if not hwnd:
        return ""
    script = f"""
    tell application "System Events"
        try
            return name of first process whose unix id is {hwnd}
        end try
    end tell
    return ""
    """
    return run_applescript(script)


def is_companion_window(hwnd: int) -> bool:
    """Verify if the process under PID is Joi's companion Tauri shell."""
    name = process_name(hwnd).strip().casefold()
    title = window_title(hwnd).strip().casefold()
    return name in COMPANION_PROCESS_NAMES or "joi" in title or "companion" in title


def is_visible_window(hwnd: int) -> bool:
    """Verify if a process window is visible on macOS."""
    if not hwnd:
        return False
    script = f"""
    tell application "System Events"
        try
            set targetProcess to first process whose unix id is {hwnd}
            return visible of targetProcess
        end try
    end tell
    return "false"
    """
    return run_applescript(script).strip().casefold() == "true"


def window_from_point(x: int, y: int) -> int:
    """Find the top-most window under a screen coordinate (macOS fallbacks to foreground window)."""
    return foreground_window()


def hide_foreground_companion_window(settle_seconds: float = 0.18) -> int | None:
    """Hide Joi's companion shell to avoid blocking screenshots."""
    hwnd = foreground_window()
    if not hwnd or not is_companion_window(hwnd):
        return None
    script = f"""
    tell application "System Events"
        try
            set targetProcess to first process whose unix id is {hwnd}
            set visible of targetProcess to false
        end try
    end tell
    """
    run_applescript(script)
    time.sleep(max(0.02, float(settle_seconds or 0.18)))
    return hwnd


def restore_window(hwnd: int | None, settle_seconds: float = 0.08) -> None:
    """Restore Joi's companion shell back to foreground after screen grabs."""
    if not hwnd:
        return
    script = f"""
    tell application "System Events"
        try
            set targetProcess to first process whose unix id is {hwnd}
            set visible of targetProcess to true
            set frontmost of targetProcess to true
        end try
    end tell
    """
    run_applescript(script)
    time.sleep(max(0.02, float(settle_seconds or 0.08)))
