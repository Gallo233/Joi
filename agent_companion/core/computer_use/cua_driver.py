from __future__ import annotations

from collections.abc import Sequence
import json
from pathlib import Path
import shutil
import struct
import subprocess
import time
from typing import Any
import uuid

from agent_companion.core.computer_use.schemas import ComputerAction, ComputerObservation, ComputerUseResult


class CuaDriverBackend:
    """Optional background computer-use backend powered by ``cua-driver``.

    CUA window screenshots and action coordinates share the same window-local
    pixel space.  The backend deliberately keeps only a pid/window binding; the
    user's project, permission and budget boundaries remain owned by Joi.
    """

    def __init__(self, workspace: Path, *, session_id: str = "") -> None:
        self.workspace = workspace.resolve()
        self.binary = shutil.which("cua-driver") or ""
        if not self.binary:
            raise RuntimeError("cua-driver is not installed")
        self.session_id = session_id or f"joi-{uuid.uuid4().hex[:12]}"
        self.pid: int | None = None
        self.window_id: int | None = None
        self.app_name = ""
        self.title = ""
        self.capture_dir = self.workspace / "data" / "agent_companion" / "captures" / "cua"
        self.capture_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def available(cls) -> bool:
        binary = shutil.which("cua-driver")
        if not binary:
            return False
        try:
            result = subprocess.run(
                [binary, "status"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def observe(self, target: str = "active_window", query: str = "") -> ComputerObservation:
        self._ensure_target()
        assert self.pid is not None and self.window_id is not None
        screenshot = self.capture_dir / f"cua-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}.png"
        payload = self._call(
            "get_window_state",
            {
                "pid": self.pid,
                "window_id": self.window_id,
                "query": query,
                "session": self.session_id,
                "screenshot_out_file": str(screenshot),
                "max_elements": 1200,
            },
            timeout=30,
        )
        if not screenshot.exists():
            candidate = _find_value(payload, "screenshot_file_path")
            if candidate:
                screenshot = Path(str(candidate)).expanduser().resolve()
        if not screenshot.exists():
            raise RuntimeError("cua-driver did not return a screenshot")
        width, height = _png_dimensions(screenshot)
        title = str(_find_value(payload, "title") or self.title or self.app_name)
        tree = str(_find_value(payload, "tree_markdown") or "")
        elements = _find_value(payload, "elements")
        rel = _relative_artifact(self.workspace, screenshot)
        return ComputerObservation(
            target=target,
            screenshot_path=screenshot,
            screenshot_rel=rel,
            width=width,
            height=height,
            title=title,
            window_handle=self.window_id,
            source="cua_driver_window",
            query=query,
            ocr={
                "provider": "cua_accessibility",
                "text": tree[:24000],
                "elements": elements[:1200] if isinstance(elements, list) else [],
            },
        )

    def perform(self, action: ComputerAction) -> ComputerUseResult:
        try:
            return self._perform(action)
        except Exception as exc:
            return ComputerUseResult(False, action=action, error=f"cua_driver_failed:{type(exc).__name__}:{str(exc)[:500]}")

    def perform_sequence(self, actions: Sequence[ComputerAction], settle_ms: int = 220) -> ComputerUseResult:
        last: ComputerUseResult | None = None
        for action in actions:
            if action.action_type == "wait":
                time.sleep(max(0, int(action.delta or settle_ms)) / 1000.0)
                continue
            last = self.perform(action)
            if not last.ok:
                return ComputerUseResult(False, action=ComputerAction("workflow"), error=last.error or "CUA workflow step failed")
            time.sleep(max(0, int(settle_ms)) / 1000.0)
        return ComputerUseResult(True, action=ComputerAction("workflow"), summary=last.summary if last else "后台流程已完成。")

    def _perform(self, action: ComputerAction) -> ComputerUseResult:
        if action.action_type == "open_app":
            return self._open_app(action)
        if action.action_type == "open_url":
            return self._open_url(action)
        if action.action_type == "wait":
            time.sleep(max(0, int(action.delta or 0)) / 1000.0)
            return ComputerUseResult(True, action=action, summary="等待后台界面响应。")
        self._ensure_target()
        assert self.pid is not None
        base: dict[str, Any] = {
            "pid": self.pid,
            "session": self.session_id,
            "delivery_mode": "background",
        }
        if self.window_id is not None:
            base["window_id"] = self.window_id
        if action.action_type in {"click", "double_click"}:
            if action.x is None or action.y is None:
                return ComputerUseResult(False, action=action, error=f"{action.action_type} requires x and y")
            base.update({"x": action.x, "y": action.y})
            tool = "right_click" if action.button == "right" else action.action_type
        elif action.action_type == "drag":
            if None in {action.x, action.y, action.end_x, action.end_y}:
                return ComputerUseResult(False, action=action, error="drag requires start and end coordinates")
            base.update({"from_x": action.x, "from_y": action.y, "to_x": action.end_x, "to_y": action.end_y, "button": action.button})
            tool = "drag"
        elif action.action_type == "type_text":
            if not action.text:
                return ComputerUseResult(False, action=action, error="type_text requires text")
            base["text"] = action.text
            tool = "type_text"
        elif action.action_type == "scroll":
            base.update({"direction": "up" if action.delta > 0 else "down", "amount": max(1, abs(action.delta or 3)), "by": "line"})
            tool = "scroll"
        elif action.action_type == "hotkey":
            if not action.keys:
                return ComputerUseResult(False, action=action, error="hotkey requires keys")
            if len(action.keys) == 1:
                base["key"] = _cua_key(action.keys[0])
                tool = "press_key"
            else:
                base["keys"] = [_cua_key(key) for key in action.keys]
                tool = "hotkey"
        else:
            return ComputerUseResult(False, action=action, error=f"unsupported CUA action: {action.action_type}")
        self._call(tool, base)
        return ComputerUseResult(True, action=action, summary="已在后台执行，并等待结果验证。")

    def _open_app(self, action: ComputerAction) -> ComputerUseResult:
        name = action.app_name.strip()
        if not name:
            return ComputerUseResult(False, action=action, error="open_app requires app name")
        payload = self._call("launch_app", {"name": name})
        self._bind_launch_result(payload, name)
        if self.pid is None:
            self._bind_window(name=name)
        return ComputerUseResult(True, action=action, summary=f"已在后台打开 {name}。")

    def _open_url(self, action: ComputerAction) -> ComputerUseResult:
        url = action.text.strip()
        if not url:
            return ComputerUseResult(False, action=action, error="open_url requires url")
        name = action.app_name.strip() or "Safari"
        payload = self._call("launch_app", {"name": name, "urls": [url]})
        self._bind_launch_result(payload, name)
        if self.pid is None:
            self._bind_window(name=name)
        return ComputerUseResult(True, action=action, summary="已在后台打开网页。")

    def _ensure_target(self) -> None:
        if self.pid is not None and self.window_id is not None:
            windows = self._windows({"pid": self.pid})
            if any(_as_int(row.get("window_id")) == self.window_id for row in windows):
                return
        self._bind_window(name=self.app_name)
        if self.pid is None or self.window_id is None:
            raise RuntimeError("CUA cannot find a target window")

    def _bind_launch_result(self, payload: Any, name: str) -> None:
        self.app_name = name
        self.pid = _as_int(_find_value(payload, "pid"))
        windows = _find_value(payload, "windows")
        if isinstance(windows, list) and windows:
            self._bind_record(_best_window([row for row in windows if isinstance(row, dict)]))

    def _bind_window(self, name: str = "") -> None:
        rows = self._windows({"on_screen_only": True})
        if name:
            match = name.casefold()
            named = [row for row in rows if match in str(row.get("app_name") or "").casefold()]
            if named:
                rows = named
        else:
            non_joi = [row for row in rows if "joi" not in str(row.get("app_name") or "").casefold()]
            if non_joi:
                rows = non_joi
        self._bind_record(_best_window(rows))

    def _bind_record(self, row: dict[str, Any] | None) -> None:
        if not row:
            return
        self.pid = _as_int(row.get("pid")) or self.pid
        self.window_id = _as_int(row.get("window_id")) or self.window_id
        self.app_name = str(row.get("app_name") or self.app_name)
        self.title = str(row.get("title") or self.title)

    def _windows(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        payload = self._call("list_windows", arguments)
        value = _find_first_list(payload, ("windows", "items", "data"))
        return [row for row in value if isinstance(row, dict)]

    def _call(self, tool: str, arguments: dict[str, Any], timeout: int = 20) -> Any:
        command = [self.binary, "call", tool, json.dumps(arguments, ensure_ascii=False)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "CUA tool failed").strip()
            raise RuntimeError(detail[:1000])
        payload = _decode_json(result.stdout)
        if _result_is_error(payload):
            raise RuntimeError(str(_find_value(payload, "error") or _content_text(payload) or "CUA tool failed")[:1000])
        return payload


def _decode_json(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(raw):
            if char not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(raw[index:])
                return value
            except json.JSONDecodeError:
                continue
    return {"content": [{"type": "text", "text": raw}]}


def _find_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_value(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_value(child, key)
            if found is not None:
                return found
    return None


def _find_first_list(value: Any, keys: tuple[str, ...]) -> list[Any]:
    for key in keys:
        found = _find_value(value, key)
        if isinstance(found, list):
            return found
    if isinstance(value, list):
        return value
    return []


def _result_is_error(payload: Any) -> bool:
    return bool(_find_value(payload, "isError") or _find_value(payload, "is_error") or _find_value(payload, "error"))


def _content_text(payload: Any) -> str:
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, list):
        return ""
    return "\n".join(str(row.get("text") or "") for row in content if isinstance(row, dict))


def _best_window(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: (_as_int(row.get("z_index")) or -1, int(bool(row.get("is_on_screen")))))


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("CUA screenshot is not a valid PNG")
    return struct.unpack(">II", header[16:24])


def _relative_artifact(workspace: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except ValueError:
        return str(path.resolve())


def _cua_key(value: str) -> str:
    aliases = {"enter": "return", "command": "cmd", "control": "ctrl", "option": "alt"}
    lowered = value.strip().casefold()
    return aliases.get(lowered, lowered)
