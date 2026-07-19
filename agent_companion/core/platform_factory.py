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

        return CuaDriverBackend(workspace, session_id=session_id)
    if sys.platform == "win32":
        from agent_companion.core.computer_use.windows import WindowsComputerUseBackend
        return WindowsComputerUseBackend(workspace, observer)
    else:
        from agent_companion.core.computer_use.mac import MacComputerUseBackend
        observer = observer or get_screen_observer(workspace)
        return MacComputerUseBackend(workspace, observer)


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
