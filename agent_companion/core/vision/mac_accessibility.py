"""macOS Accessibility Observer using AXUIElement API via ctypes.

Enumerates UI controls (buttons, links, text fields, etc.) from the
frontmost application's accessibility tree. Falls back to AppleScript
System Events when AXUIElement is not available or fails.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import subprocess
import sys
from typing import Any

from agent_companion.core.vision.accessibility import (
    AccessibleElement,
    AccessibilityObserver,
    AccessibilitySnapshot,
)

# ---------------------------------------------------------------------------
# AXUIElement ctypes bindings (macOS ApplicationServices framework)
# ---------------------------------------------------------------------------

_AX_OK = 0
_kAXErrorSuccess = 0

# AXUIElement role constants (CFString values we compare against)
_CLICKABLE_ROLES = frozenset({
    "AXButton", "AXLink", "AXRadioButton", "AXCheckBox",
    "AXMenuItem", "AXMenuBarItem", "AXTab", "AXDisclosureTriangle",
    "AXPopUpButton", "AXComboBox", "AXSlider", "AXColorWell",
    "AXToggle", "AXSwitch",
})
_ACTIONABLE_ROLES = _CLICKABLE_ROLES | frozenset({
    "AXTextField", "AXTextArea", "AXSearchField", "AXSecureTextField",
    "AXScrollArea", "AXScrollBar", "AXTable", "AXOutline",
    "AXList", "AXMenu", "AXMenuBar", "AXToolbar", "AXTabGroup",
})

_ax_lib: Any = None
_cf_lib: Any = None
_ax_loaded = False


def _load_ax_libs() -> bool:
    """Attempt to load ApplicationServices AX functions. Returns True on success."""
    global _ax_lib, _cf_lib, _ax_loaded
    if _ax_loaded:
        return _ax_lib is not None
    _ax_loaded = True
    try:
        _ax_lib = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        _cf_lib = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

        # CoreFoundation string functions
        _cf_lib.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        _cf_lib.CFStringCreateWithCString.restype = ctypes.c_void_p
        _cf_lib.CFRelease.argtypes = [ctypes.c_void_p]
        _cf_lib.CFRelease.restype = None
        _cf_lib.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        _cf_lib.CFArrayGetCount.restype = ctypes.c_long
        _cf_lib.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        _cf_lib.CFArrayGetValueAtIndex.restype = ctypes.c_void_p

        # AXUIElement functions
        _ax_lib.AXUIElementCreateApplication.argtypes = [ctypes.c_int32]
        _ax_lib.AXUIElementCreateApplication.restype = ctypes.c_void_p

        _ax_lib.AXUIElementCopyAttributeValue.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        ]
        _ax_lib.AXUIElementCopyAttributeValue.restype = ctypes.c_int32

        _ax_lib.AXUIElementCopyActionNames.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        ]
        _ax_lib.AXUIElementCopyActionNames.restype = ctypes.c_int32

        _ax_lib.AXUIElementIsAttributeSettable.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)
        ]
        _ax_lib.AXUIElementIsAttributeSettable.restype = ctypes.c_int32

        _ax_lib.AXUIElementPerformAction.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        _ax_lib.AXUIElementPerformAction.restype = ctypes.c_int32

        _ax_lib.AXValueGetValue.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)
        ]
        _ax_lib.AXValueGetValue.restype = ctypes.c_bool

        # kAXValueCGPointType = 1, kAXValueCGSizeType = 2
        return True
    except Exception:
        _ax_lib = None
        _cf_lib = None
        return False


def _cfstr(s: str) -> ctypes.c_void_p:
    """Create a CFString from a Python string."""
    return _cf_lib.CFStringCreateWithCString(None, s.encode("utf-8"), 0x08000100)


def _ax_get_attr(element: ctypes.c_void_p, attr_name: str) -> ctypes.c_void_p | None:
    """Get an AX attribute value. Returns the CFTypeRef or None."""
    cf_attr = _cfstr(attr_name)
    value = ctypes.c_void_p()
    err = _ax_lib.AXUIElementCopyAttributeValue(element, cf_attr, ctypes.byref(value))
    _cf_lib.CFRelease(cf_attr)
    if err == _kAXErrorSuccess and value.value:
        return value
    return None


def _ax_get_string(element: ctypes.c_void_p, attr_name: str) -> str:
    """Get an AX string attribute."""
    val = _ax_get_attr(element, attr_name)
    if val is None:
        return ""
    try:
        # Convert CFString to C string
        buf = ctypes.create_string_buffer(512)
        ok = _cf_lib.CFStringGetCString(val, buf, 512, 0x08000100)
        _cf_lib.CFRelease(val)
        if ok:
            return buf.value.decode("utf-8", errors="replace")
    except Exception:
        try:
            _cf_lib.CFRelease(val)
        except Exception:
            pass
    return ""


def _ax_get_children(element: ctypes.c_void_p) -> list[ctypes.c_void_p]:
    """Get AX children as a list of AXUIElement refs."""
    children_ref = _ax_get_attr(element, "AXChildren")
    if children_ref is None:
        return []
    try:
        count = _cf_lib.CFArrayGetCount(children_ref)
        result = []
        for i in range(min(count, 50)):  # cap at 50 children per node
            child = _cf_lib.CFArrayGetValueAtIndex(children_ref, i)
            if child:
                # Retain each child so it stays valid
                _cf_lib.CFRetain(child)
                result.append(child)
        _cf_lib.CFRelease(children_ref)
        return result
    except Exception:
        try:
            _cf_lib.CFRelease(children_ref)
        except Exception:
            pass
        return []


def _ax_get_position(element: ctypes.c_void_p) -> tuple[float, float] | None:
    """Get AX position as (x, y) in screen coordinates."""
    pos_ref = _ax_get_attr(element, "AXPosition")
    if pos_ref is None:
        return None
    try:
        from ctypes import Structure, c_double
        class CGPoint(Structure):
            _fields_ = [("x", c_double), ("y", c_double)]
        point = CGPoint()
        size_type = ctypes.c_uint32(1)  # kAXValueCGPointType = 1
        ok = _ax_lib.AXValueGetValue(pos_ref, 1, ctypes.byref(point), ctypes.byref(size_type))
        _cf_lib.CFRelease(pos_ref)
        if ok:
            return (point.x, point.y)
    except Exception:
        try:
            _cf_lib.CFRelease(pos_ref)
        except Exception:
            pass
    return None


def _ax_get_size(element: ctypes.c_void_p) -> tuple[float, float] | None:
    """Get AX size as (width, height)."""
    size_ref = _ax_get_attr(element, "AXSize")
    if size_ref is None:
        return None
    try:
        from ctypes import Structure, c_double
        class CGSize(Structure):
            _fields_ = [("width", c_double), ("height", c_double)]
        size = CGSize()
        size_type = ctypes.c_uint32(2)  # kAXValueCGSizeType = 2
        ok = _ax_lib.AXValueGetValue(size_ref, 2, ctypes.byref(size), ctypes.byref(size_type))
        _cf_lib.CFRelease(size_ref)
        if ok:
            return (size.width, size.height)
    except Exception:
        try:
            _cf_lib.CFRelease(size_ref)
        except Exception:
            pass
    return None


def _ax_has_actions(element: ctypes.c_void_p) -> bool:
    """Check if an AX element has any actions (like AXPress)."""
    actions_ref = ctypes.c_void_p()
    err = _ax_lib.AXUIElementCopyActionNames(element, ctypes.byref(actions_ref))
    if err != _kAXErrorSuccess or not actions_ref.value:
        return False
    try:
        count = _cf_lib.CFArrayGetCount(actions_ref)
        _cf_lib.CFRelease(actions_ref)
        return count > 0
    except Exception:
        try:
            _cf_lib.CFRelease(actions_ref)
        except Exception:
            pass
    return False


def _ax_release(element: ctypes.c_void_p) -> None:
    """Release an AXUIElement reference."""
    try:
        _cf_lib.CFRelease(element)
    except Exception:
        pass


# Also need CFRetain
def _ensure_cf_retain():
    if _cf_lib and not hasattr(_cf_lib, '_cfr_set'):
        try:
            _cf_lib.CFRetain.argtypes = [ctypes.c_void_p]
            _cf_lib.CFRetain.restype = ctypes.c_void_p
            _cf_lib.CFStringGetCString.argtypes = [
                ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32
            ]
            _cf_lib.CFStringGetCString.restype = ctypes.c_bool
            _cf_lib._cfr_set = True
        except Exception:
            pass


class MacAccessibilityObserver(AccessibilityObserver):
    """macOS accessibility tree observer using AXUIElement via ctypes.

    Falls back to AppleScript System Events if AXUIElement is unavailable
    (e.g. accessibility permission not granted).
    """

    def __init__(self, max_depth: int = 5, max_elements: int = 100) -> None:
        self.max_depth = max(1, int(max_depth))
        self.max_elements = max(1, int(max_elements))
        self._ax_available: bool | None = None

    def observe(self, window_handle: int | None = None, title: str = "") -> AccessibilitySnapshot:
        if sys.platform != "darwin":
            return AccessibilitySnapshot(
                "unavailable", title=title, window_handle=window_handle,
                error="mac_only",
            )

        # Try AXUIElement first (provides richer data)
        if self._check_ax_available():
            try:
                result = self._observe_ax(window_handle, title)
                if result.status == "success":
                    return result
            except Exception:
                pass

        # Fallback to AppleScript (always works, less detail)
        try:
            return self._observe_applescript(window_handle, title)
        except Exception as exc:
            return AccessibilitySnapshot(
                "failed", title=title, window_handle=window_handle,
                error=f"both_methods_failed: {exc}",
            )

    def _check_ax_available(self) -> bool:
        if self._ax_available is not None:
            return self._ax_available
        self._ax_available = _load_ax_libs()
        if self._ax_available:
            _ensure_cf_retain()
        return self._ax_available

    # -------------------------------------------------------------------
    # AXUIElement path (rich: buttons, roles, exact bounds)
    # -------------------------------------------------------------------
    def _observe_ax(self, window_handle: int | None, title: str) -> AccessibilitySnapshot:
        import os

        pid = int(window_handle or 0)
        if pid <= 0:
            # Get frontmost app PID
            pid = self._frontmost_pid()
        if pid <= 0:
            return AccessibilitySnapshot("failed", title=title, error="no_pid")

        app_ref = _ax_lib.AXUIElementCreateApplication(pid)
        if not app_ref:
            return AccessibilitySnapshot("failed", title=title, window_handle=pid, error="no_app_ref")

        try:
            # Get app title if not provided
            if not title:
                title = _ax_get_string(app_ref, "AXTitle")

            # Get windows
            windows_ref = _ax_get_attr(app_ref, "AXWindows")
            if windows_ref is None:
                return AccessibilitySnapshot(
                    "failed", title=title, window_handle=pid, error="no_windows"
                )

            elements: list[AccessibleElement] = []
            try:
                win_count = _cf_lib.CFArrayGetCount(windows_ref)
                for wi in range(min(win_count, 5)):
                    win_ref = _cf_lib.CFArrayGetValueAtIndex(windows_ref, wi)
                    if win_ref:
                        _cf_lib.CFRetain(win_ref)
                        self._walk_ax(win_ref, elements, depth=0)
                        _ax_release(win_ref)
            finally:
                _cf_lib.CFRelease(windows_ref)

            if not elements:
                return AccessibilitySnapshot(
                    "success", title=title, window_handle=pid, elements=[],
                    error="no_elements_found",
                )

            return AccessibilitySnapshot(
                "success", title=title, window_handle=pid,
                elements=elements[:self.max_elements],
            )
        finally:
            _ax_release(app_ref)

    def _walk_ax(self, element: ctypes.c_void_p, elements: list[AccessibleElement], depth: int) -> None:
        if len(elements) >= self.max_elements:
            return

        el = self._element_from_ax(element)
        if el is not None:
            elements.append(el)

        if depth >= self.max_depth:
            return

        children = _ax_get_children(element)
        for child in children:
            if len(elements) >= self.max_elements:
                _ax_release(child)
                continue
            self._walk_ax(child, elements, depth + 1)
            _ax_release(child)

    def _element_from_ax(self, element: ctypes.c_void_p) -> AccessibleElement | None:
        role = _ax_get_string(element, "AXRole")
        title = _ax_get_string(element, "AXTitle") or _ax_get_string(element, "AXDescription")
        value = _ax_get_string(element, "AXValue")

        name = title or value or ""
        if not name and role not in _CLICKABLE_ROLES:
            return None

        position = _ax_get_position(element)
        size = _ax_get_size(element)
        bounds = None
        if position and size:
            px, py = position
            sw, sh = size
            if sw > 0 and sh > 0:
                bounds = (int(px), int(py), int(sw), int(sh))

        if bounds is not None and (bounds[2] <= 0 or bounds[3] <= 0):
            return None

        enabled = True  # AXUIElement doesn't have a simple "enabled" check; assume True
        clickable = role in _CLICKABLE_ROLES or _ax_has_actions(element)

        if not name and not clickable:
            return None

        confidence = 0.90 if clickable else 0.70
        return AccessibleElement(
            name=name[:160],
            role=role[:80] if role else "unknown",
            bounds=bounds,
            enabled=enabled,
            clickable=clickable,
            source="ax_accessibility",
            confidence=confidence,
        )

    @staticmethod
    def _frontmost_pid() -> int:
        """Get the frontmost application PID using AppleScript."""
        script = '''
        tell application "System Events"
            try
                set frontProc to first process whose frontmost is true
                return unix id of frontProc
            end try
        end tell
        return 0
        '''
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3.0,
            )
            return int(proc.stdout.strip()) if proc.stdout.strip() else 0
        except Exception:
            return 0

    # -------------------------------------------------------------------
    # AppleScript fallback path (coarser but always works)
    # -------------------------------------------------------------------
    def _observe_applescript(self, window_handle: int | None, title: str) -> AccessibilitySnapshot:
        pid = int(window_handle or 0)

        # Get frontmost PID if not provided
        if pid <= 0:
            pid = self._frontmost_pid()
        if pid <= 0:
            return AccessibilitySnapshot("failed", title=title, error="no_pid_applescript")

        # Get app name for targeted querying
        app_name = self._get_app_name(pid)

        # Try to get UI elements via System Events with entire contents
        # This is slower but gives us actual buttons/controls
        elements = self._get_elements_via_system_events(pid)

        if not elements:
            # Fallback: just enumerate windows
            elements = self._get_windows_via_system_events(pid)

        if not title:
            title = app_name or f"PID {pid}"

        return AccessibilitySnapshot(
            "success" if elements else "failed",
            title=title,
            window_handle=pid,
            elements=elements[:self.max_elements],
        )

    def _get_app_name(self, pid: int) -> str:
        script = f'''
        tell application "System Events"
            try
                return name of first process whose unix id is {pid}
            end try
        end tell
        return ""
        '''
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3.0,
            )
            return proc.stdout.strip()
        except Exception:
            return ""

    def _get_elements_via_system_events(self, pid: int) -> list[AccessibleElement]:
        """Get UI elements using System Events entire contents (slow but comprehensive)."""
        # This script gets all UI elements of the frontmost window
        # with their attributes. We limit depth for performance.
        script = f'''
        tell application "System Events"
            try
                set targetProc to first process whose unix id is {pid}
                set winCount to count of windows of targetProc
                if winCount = 0 then return ""

                set targetWin to first window of targetProc
                set allElements to entire contents of targetWin
                set output to ""
                set elemCount to count of allElements
                if elemCount > {self.max_elements} then set elemCount to {self.max_elements}

                repeat with i from 1 to elemCount
                    try
                        set elem to item i of allElements
                        set elemRole to ""
                        set elemTitle to ""
                        set elemPos to ""
                        set elemSize to ""

                        try
                            set elemRole to role description of elem
                        end try
                        if elemRole = "" then
                            try
                                set elemRole to class of elem as text
                            end try
                        end if

                        try
                            set elemTitle to title of elem
                        end try
                        if elemTitle = "" then
                            try
                                set elemTitle to description of elem
                            end try
                        end if
                        if elemTitle = "" then
                            try
                                set elemTitle to value of elem as text
                            end try
                        end if

                        try
                            set elemPos to position of elem
                            set elemSize to size of elem
                        end try

                        if elemRole is not "" or elemTitle is not "" then
                            set output to output & elemRole & "||" & elemTitle & "||"
                            if elemPos is not "" then
                                set output to output & (item 1 of elemPos) & "," & (item 2 of elemPos) & ","
                                set output to output & (item 1 of elemSize) & "," & (item 2 of elemSize)
                            end if
                            set output to output & "\\n"
                        end if
                    end try
                end repeat

                return output
            end try
        end tell
        return ""
        '''

        elements: list[AccessibleElement] = []
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10.0,
            )
            raw = proc.stdout.strip()
            if not raw:
                return elements

            for line in raw.split("\n"):
                line = line.strip()
                if not line:
                    continue
                parts = line.split("||")
                if len(parts) < 2:
                    continue
                role = parts[0].strip()
                name = parts[1].strip()
                bounds = None
                if len(parts) >= 3 and parts[2].strip():
                    try:
                        coords = [int(float(c.strip())) for c in parts[2].split(",") if c.strip()]
                        if len(coords) == 4 and coords[2] > 0 and coords[3] > 0:
                            bounds = (coords[0], coords[1], coords[2], coords[3])
                    except (ValueError, IndexError):
                        pass

                if not name and role.lower() not in {"button", "link", "checkbox", "radio button", "text field"}:
                    continue

                clickable = self._role_is_clickable(role)
                elements.append(AccessibleElement(
                    name=name[:160],
                    role=role[:80] or "unknown",
                    bounds=bounds,
                    enabled=True,
                    clickable=clickable,
                    source="applescript_events",
                    confidence=0.82 if clickable else 0.60,
                ))
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

        return elements

    def _get_windows_via_system_events(self, pid: int) -> list[AccessibleElement]:
        """Fallback: just get window rectangles."""
        script = f'''
        tell application "System Events"
            try
                set targetProc to first process whose unix id is {pid}
                set winList to every window of targetProc
                set output to ""
                repeat with w in winList
                    try
                        set wTitle to name of w
                        set wBounds to bounds of w
                        set output to output & wTitle & "||" & (item 1 of wBounds) & "," & (item 2 of wBounds) & "," & (item 3 of wBounds) & "," & (item 4 of wBounds) & "\\n"
                    end try
                end repeat
                return output
            end try
        end tell
        return ""
        '''

        elements: list[AccessibleElement] = []
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=5.0,
            )
            raw = proc.stdout.strip()
            for line in raw.split("\n"):
                line = line.strip()
                if not line or "||" not in line:
                    continue
                parts = line.split("||", 1)
                name = parts[0].strip()
                bounds = None
                if len(parts) >= 2:
                    try:
                        coords = [int(float(c.strip())) for c in parts[1].split(",") if c.strip()]
                        if len(coords) == 4 and coords[2] > 0 and coords[3] > 0:
                            bounds = (coords[0], coords[1], coords[2] - coords[0], coords[3] - coords[1])
                    except (ValueError, IndexError):
                        pass
                if name:
                    elements.append(AccessibleElement(
                        name=name[:160],
                        role="window",
                        bounds=bounds,
                        enabled=True,
                        clickable=False,
                        source="applescript_windows",
                        confidence=0.50,
                    ))
        except Exception:
            pass

        return elements

    @staticmethod
    def _role_is_clickable(role: str) -> bool:
        """Determine if a System Events role description suggests clickability."""
        role_lower = (role or "").strip().casefold()
        click_keywords = {
            "button", "link", "checkbox", "radio", "tab", "menu",
            "pop up", "popup", "slider", "toggle", "disclosure",
            "combo", "segment", "toolbar",
        }
        return any(kw in role_lower for kw in click_keywords)
