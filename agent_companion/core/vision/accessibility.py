from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import sys
import time
from typing import Any, Protocol


@dataclass(frozen=True)
class AccessibleElement:
    name: str
    role: str
    bounds: tuple[int, int, int, int] | None = None
    enabled: bool = True
    clickable: bool = False
    source: str = "accessibility"
    confidence: float = 0.75

    def to_agent_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "role": self.role,
            "enabled": self.enabled,
            "clickable": self.clickable,
            "source": self.source,
            "confidence": round(float(self.confidence), 3),
        }
        if self.bounds is not None:
            payload["bounds"] = list(self.bounds)
        return payload


@dataclass(frozen=True)
class AccessibilitySnapshot:
    status: str
    title: str = ""
    window_handle: int | None = None
    elements: list[AccessibleElement] = field(default_factory=list)
    error: str = ""
    created_at: float = field(default_factory=time.time)

    def to_agent_state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "title": self.title,
            "window_handle": self.window_handle,
            "elements": [element.to_agent_state() for element in self.elements],
            "error": self.error,
            "created_at": self.created_at,
        }

    def detail_text(self) -> str:
        if self.status == "success":
            return f"UI控件：识别到 {len(self.elements)} 个候选控件。"
        if self.status == "unavailable":
            return "UI控件：当前环境未启用 accessibility 观察。"
        return "UI控件：观察失败，已降级使用截图和 OCR。"


class AccessibilityObserver(Protocol):
    def observe(self, window_handle: int | None = None, title: str = "") -> AccessibilitySnapshot:
        ...


class UnavailableAccessibilityObserver:
    def __init__(self, reason: str = "accessibility_unavailable") -> None:
        self.reason = reason

    def observe(self, window_handle: int | None = None, title: str = "") -> AccessibilitySnapshot:
        return AccessibilitySnapshot("unavailable", title=title, window_handle=window_handle, error=self.reason)


class WindowsAccessibilityObserver:
    def __init__(self, max_depth: int = 4, max_elements: int = 120) -> None:
        self.max_depth = max(1, int(max_depth))
        self.max_elements = max(1, int(max_elements))

    def observe(self, window_handle: int | None = None, title: str = "") -> AccessibilitySnapshot:
        if sys.platform != "win32":
            return AccessibilitySnapshot("unavailable", title=title, window_handle=window_handle, error="windows_only")
        hwnd = int(window_handle or self._foreground_window() or 0)
        if hwnd <= 0:
            return AccessibilitySnapshot("unavailable", title=title, window_handle=None, error="no_active_window")
        try:
            import uiautomation as auto  # type: ignore[import-not-found]
        except Exception:
            return AccessibilitySnapshot("unavailable", title=title, window_handle=hwnd, error="uiautomation_missing")

        try:
            root = auto.ControlFromHandle(hwnd)
            elements: list[AccessibleElement] = []
            self._walk(root, elements, depth=0)
            return AccessibilitySnapshot("success", title=title, window_handle=hwnd, elements=elements[: self.max_elements])
        except Exception:
            return AccessibilitySnapshot("failed", title=title, window_handle=hwnd, error="uiautomation_failed")

    def _walk(self, control: Any, elements: list[AccessibleElement], depth: int) -> None:
        if len(elements) >= self.max_elements:
            return
        element = self._element_from_control(control)
        if element is not None:
            elements.append(element)
        if depth >= self.max_depth:
            return
        try:
            children = control.GetChildren()
        except Exception:
            children = []
        for child in children or []:
            self._walk(child, elements, depth + 1)
            if len(elements) >= self.max_elements:
                break

    @staticmethod
    def _element_from_control(control: Any) -> AccessibleElement | None:
        name = str(getattr(control, "Name", "") or "").strip()
        role = str(getattr(control, "ControlTypeName", "") or getattr(control, "ClassName", "") or "").strip()
        bounds = _bounds_from_control(control)
        enabled = bool(getattr(control, "IsEnabled", True))
        clickable = _looks_clickable(role, control)
        if not name and not clickable:
            return None
        if bounds is not None and (bounds[2] <= 0 or bounds[3] <= 0):
            return None
        confidence = 0.88 if clickable else 0.68
        return AccessibleElement(
            name=name[:160],
            role=role[:80],
            bounds=bounds,
            enabled=enabled,
            clickable=clickable,
            confidence=confidence,
        )

    @staticmethod
    def _foreground_window() -> int:
        return int(ctypes.windll.user32.GetForegroundWindow())


def _bounds_from_control(control: Any) -> tuple[int, int, int, int] | None:
    try:
        rect = getattr(control, "BoundingRectangle", None)
        if rect is None:
            return None
        left = int(getattr(rect, "left", getattr(rect, "Left", 0)))
        top = int(getattr(rect, "top", getattr(rect, "Top", 0)))
        right = int(getattr(rect, "right", getattr(rect, "Right", 0)))
        bottom = int(getattr(rect, "bottom", getattr(rect, "Bottom", 0)))
        if right <= left or bottom <= top:
            return None
        return (left, top, right - left, bottom - top)
    except Exception:
        return None


def _looks_clickable(role: str, control: Any) -> bool:
    try:
        getter = getattr(control, "GetInvokePattern", None)
        if not callable(getter):
            return False
        return getter() is not None
    except Exception:
        return False
