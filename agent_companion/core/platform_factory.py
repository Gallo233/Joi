from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_companion.core.computer_use import ComputerUseBackend
    from agent_companion.core.vision import AccessibilityObserver, VisionObserver


def get_computer_backend(workspace: Path, observer: VisionObserver | None = None) -> ComputerUseBackend:
    """Instantiate the dynamic computer use automation backend for the current platform."""
    if sys.platform == "win32":
        from agent_companion.core.computer_use.windows import WindowsComputerUseBackend
        return WindowsComputerUseBackend(workspace, observer)
    else:
        from agent_companion.core.computer_use.mac import MacComputerUseBackend
        return MacComputerUseBackend(workspace, observer)


def get_screen_observer(workspace: Path) -> VisionObserver:
    """Instantiate the dynamic screen visual observer for the current platform."""
    if sys.platform == "win32":
        from agent_companion.core.vision.windows import WindowsScreenObserver
        return WindowsScreenObserver(workspace)
    else:
        from agent_companion.core.vision.mac import MacScreenObserver
        return MacScreenObserver(workspace)


def get_accessibility_observer() -> AccessibilityObserver:
    """Instantiate the accessibility tree snapshot observer for the current platform."""
    if sys.platform == "win32":
        from agent_companion.core.vision.accessibility import WindowsAccessibilityObserver
        return WindowsAccessibilityObserver()
    else:
        from agent_companion.core.vision.accessibility import MacAccessibilityObserver
        return MacAccessibilityObserver()
