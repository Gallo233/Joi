from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
import inspect
import sys
from typing import Any


@contextmanager
def companion_hidden_for_target_observation() -> Iterator[None]:
    """Keep Joi out of the way while an action and its verification screenshot run."""
    hidden_handle: int | None = None
    restore = None
    try:
        if sys.platform == "darwin":
            from agent_companion.core.windows_focus_mac import hide_foreground_companion_window, restore_window

            hidden_handle = hide_foreground_companion_window(settle_seconds=0.18)
            restore = restore_window
        elif sys.platform.startswith("win"):
            from agent_companion.core.windows_focus import hide_foreground_companion_window, restore_window

            hidden_handle = hide_foreground_companion_window(settle_seconds=0.18)
            restore = restore_window
    except Exception:
        hidden_handle = None
        restore = None

    try:
        yield
    finally:
        if hidden_handle is not None and restore is not None:
            try:
                _restore_hidden_window(restore, hidden_handle, activate=True)
            except Exception:
                pass


def _restore_hidden_window(restore: Callable[..., Any], hidden_handle: int, *, activate: bool = True) -> None:
    try:
        parameters = inspect.signature(restore).parameters
    except (TypeError, ValueError):
        try:
            restore(hidden_handle, activate=activate)
        except TypeError:
            restore(hidden_handle)
        return
    if "activate" in parameters:
        restore(hidden_handle, activate=activate)
    else:
        restore(hidden_handle)
