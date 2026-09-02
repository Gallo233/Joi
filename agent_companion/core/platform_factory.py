from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from agent_companion.core.vision.schemas import VisionObservation

if TYPE_CHECKING:
    from agent_companion.core.computer_use import ComputerUseBackend
    from agent_companion.core.vision import AccessibilityObserver, VisionObserver


def get_computer_backend(
    workspace: Path,
    observer: VisionObserver | None = None,
    *,
    driver: str = "native",
    session_id: str = "",
) -> ComputerUseBackend:
    """Instantiate the dynamic computer use automation backend for the current platform."""
    if driver == "cua":
        from agent_companion.core.computer_use.cua_driver import CuaDriverBackend
        from agent_companion.core.computer_use.driver_fallback import FallbackComputerUseBackend

        # CUA is optional, so a session using it keeps the native driver in
        # reserve -- but only reaches for it when the failed call provably
        # never touched the application (see driver_fallback).
        return FallbackComputerUseBackend(
            CuaDriverBackend(workspace, session_id=session_id),
            lambda: _native_computer_backend(workspace, observer),
        )
    return _native_computer_backend(workspace, observer)


def _native_computer_backend(workspace: Path, observer: VisionObserver | None = None) -> ComputerUseBackend:
    if sys.platform == "win32":
        from agent_companion.core.computer_use.windows import WindowsComputerUseBackend
        return WindowsComputerUseBackend(workspace, observer)
    from agent_companion.core.computer_use.mac import MacComputerUseBackend
    return MacComputerUseBackend(workspace, observer or get_screen_observer(workspace))


def get_screen_observer(workspace: Path) -> VisionObserver:
    """Instantiate the dynamic screen visual observer for the current platform."""
    if sys.platform == "win32":
        from agent_companion.core.vision.windows import WindowsScreenObserver
        return WindowsScreenObserver(workspace)
    else:
        try:
            from agent_companion.core.vision.mac import MacScreenObserver

            return MacScreenObserver(workspace)
        except Exception as exc:
            return UnavailableScreenObserver(workspace, f"mac_screen_observer_unavailable:{type(exc).__name__}")


def get_accessibility_observer() -> AccessibilityObserver:
    """Instantiate the accessibility tree snapshot observer for the current platform."""
    if sys.platform == "win32":
        from agent_companion.core.vision.accessibility import WindowsAccessibilityObserver
        return WindowsAccessibilityObserver()
    else:
        from agent_companion.core.vision.accessibility import MacAccessibilityObserver
        return MacAccessibilityObserver()


class UnavailableScreenObserver:
    """Fail-soft observer used when optional platform capture dependencies are missing."""

    def __init__(self, workspace: Path, reason: str = "screen_observer_unavailable") -> None:
        self.workspace = workspace.resolve()
        self.reason = reason

    def observe(self, target: str = "active_window", query: str = "") -> VisionObservation:
        raise RuntimeError(self.reason)
