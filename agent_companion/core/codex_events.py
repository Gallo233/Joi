from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any


PERMISSION_FAIL_CLOSED_MESSAGE = "Codex 需要外部权限确认，Joi 暂不能继续。"

_EVENT_FALLBACKS = {
    "started": "Codex 运行已开始。",
    "progress": "Codex 正在处理写码任务。",
    "final": "Codex 生成了最终结果。",
    "error": "Codex 报告了运行错误。",
    "permission_request": "Codex 请求一个外部权限确认。",
    "unknown": "Codex 发出了未识别的事件。",
    "malformed": "Codex 发出了无法解析的事件。",
}

_PATH_RE = re.compile(r"(?<!:)\/(?:Users|home|private|tmp|var|Volumes)\/[^\s]+|\b[A-Za-z]:[\\/][^\s]+")
_REL_PATH_RE = re.compile(r"\bdata/[^\s]+")
_FILE_RE = re.compile(r"\b[\w.-]+\.(?:png|jpg|jpeg|webp|bmp|gif|ppm|jsonl|json|log|txt|yaml|yml|py|bat|ps1|sh|gguf|safetensors|ckpt|pth|onnx|bin)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"sk-[A-Za-z0-9_-]+|\b(?:api[_-]?key|secret|token|bearer)\b", re.IGNORECASE)
_ID_RE = re.compile(r"\b(?:task|approval|selection|codex|run|resume)[-_]?[0-9a-f]{6,}\b", re.IGNORECASE)
_COORD_RE = re.compile(r"\b\d{1,5}\s*[,，]\s*\d{1,5}\b")
_FLAG_RE = re.compile(r"--[A-Za-z0-9-]+")
_UNSAFE_RE = re.compile(
    r"[\{\}\[\]\"]|"
    r"(?<!:)\/(?:Users|home|private|tmp|var|Volumes)\/|"
    r"\b[A-Za-z]:[\\/]|"
    r"\bdata/|"
    r"\b[\w.-]+\.(?:png|jpg|jpeg|webp|bmp|gif|ppm|jsonl|json|log|txt|yaml|yml|py|bat|ps1|sh|gguf|safetensors|ckpt|pth|onnx|bin)\b|"
    r"sk-[A-Za-z0-9_-]+|"
    r"\b(?:api[_-]?key|secret|token|bearer|traceback|stderr|stdout)\b|"
    r"\b(?:task|approval|selection|codex|run|resume)[-_]?[0-9a-f]{6,}\b|"
    r"\b\d{1,5}\s*[,，]\s*\d{1,5}\b|"
    r"--[A-Za-z0-9-]+",
    re.IGNORECASE,
)


def parse_codex_jsonl(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    events: list[dict[str, Any]] = []
    permission: dict[str, Any] | None = None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return events, None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            events.append(_event("malformed", _EVENT_FALLBACKS["malformed"]))
            continue
        if not isinstance(payload, dict):
            events.append(_event("unknown", _EVENT_FALLBACKS["unknown"]))
            continue

        category = _event_category(payload)
        event = _event(category, _extract_summary(payload, category), _event_timestamp(payload))
        events.append(event)
        if category == "permission_request" and permission is None:
            permission = _permission_request(payload)
    return events, permission


def build_codex_run_state(
    *,
    stdout_path: Path,
    stderr_path: Path,
    final_path: Path,
    returncode: int | None,
    elapsed_seconds: float,
    artifact_labels: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    events, permission = parse_codex_jsonl(stdout_path)
    if not events:
        events = [_event("progress", "Codex 没有输出结构化事件。")]

    if permission is not None and permission.get("resumable"):
        status = "permission_required"
        safe_summary = "Codex 需要你确认一个外部权限请求。"
    elif permission is not None:
        status = "fail_closed"
        safe_summary = PERMISSION_FAIL_CLOSED_MESSAGE
    elif returncode == 0:
        status = "completed"
        safe_summary = "Codex 已完成本次写码任务。"
    else:
        status = "failed"
        safe_summary = f"Codex 运行失败，退出码 {returncode}。" if returncode is not None else "Codex 运行失败。"

    final_available = _has_content(final_path)
    stderr_available = _has_content(stderr_path)
    state = {
        "status": status,
        "elapsed_seconds": round(max(0.0, elapsed_seconds), 2),
        "returncode": returncode,
        "safe_summary": safe_summary,
        "permission_required": bool(permission is not None and permission.get("resumable")),
        "permission_detected": permission is not None,
        "artifacts": artifact_labels,
        "events": events[-24:],
        "signals": {
            "final_summary": "available" if final_available else "missing",
            "error_output": "available" if stderr_available else "empty",
        },
    }
    if permission is not None:
        state["permission"] = {
            "resumable": bool(permission.get("resumable")),
            "permission_hash": str(permission.get("permission_hash") or ""),
            "tool": str(permission.get("tool") or "external_permission"),
        }
    return state, permission


def codex_card_body(codex_run: dict[str, Any]) -> str:
    lines = [str(codex_run.get("safe_summary") or "Codex 状态已更新。")]
    returncode = codex_run.get("returncode")
    if returncode is not None:
        lines.append(f"退出码：{returncode}")
    events = codex_run.get("events") if isinstance(codex_run.get("events"), list) else []
    for event in events[-5:]:
        if not isinstance(event, dict):
            continue
        summary = str(event.get("summary") or "").strip()
        if summary:
            lines.append(f"- {summary}")
    artifacts = codex_run.get("artifacts") if isinstance(codex_run.get("artifacts"), list) else []
    labels = [str(item.get("label") or "") for item in artifacts if isinstance(item, dict) and item.get("label")]
    if labels:
        lines.append("产物：" + "、".join(labels[:4]))
    return "\n".join(lines)


def sanitized_codex_text(value: object, fallback: str = "Codex 状态已更新。") -> str:
    if isinstance(value, (dict, list, tuple, set)):
        return fallback
    cleaned = " ".join(str(value or "").split()).strip()
    if not cleaned:
        return fallback
    cleaned = _PATH_RE.sub("[path]", cleaned)
    cleaned = _REL_PATH_RE.sub("[path]", cleaned)
    cleaned = _FILE_RE.sub("[file]", cleaned)
    cleaned = _TOKEN_RE.sub("[secret]", cleaned)
    cleaned = _ID_RE.sub("[id]", cleaned)
    cleaned = _COORD_RE.sub("[point]", cleaned)
    cleaned = _FLAG_RE.sub("[flag]", cleaned)
    if _UNSAFE_RE.search(cleaned):
        return fallback
    return cleaned[:180]


def codex_permission_approval_arguments(goal: str, permission: dict[str, Any]) -> dict[str, str]:
    return {
        "goal": goal,
        "codex_permission_decision": "approved",
        "codex_permission_hash": str(permission.get("permission_hash") or ""),
        "codex_resume_token": str(permission.get("resume_token") or ""),
    }


def codex_cancel_run_state(status: str) -> dict[str, Any]:
    labels = {
        "denied": "你拒绝了 Codex 权限请求，我没有继续执行。",
        "expired": "Codex 权限确认已经过期，我没有继续执行。",
        "mismatch": "Codex 权限确认已经失效，我没有继续执行。",
    }
    event_type = f"permission_{status}"
    summary = labels.get(status, "Codex 权限请求已停止。")
    return {
        "status": status,
        "elapsed_seconds": 0,
        "returncode": None,
        "safe_summary": summary,
        "permission_required": False,
        "artifacts": [],
        "events": [_event(event_type, summary)],
    }


def _event(category: str, summary: str, timestamp: float | None = None) -> dict[str, Any]:
    return {
        "category": category,
        "event_type": category,
        "timestamp": timestamp or time.time(),
        "summary": sanitized_codex_text(summary, _EVENT_FALLBACKS.get(category, _EVENT_FALLBACKS["unknown"])),
    }


def _event_category(payload: dict[str, Any]) -> str:
    if _has_permission_marker(payload):
        return "permission_request"
    markers = _string_markers(payload)
    if any(token in markers for token in ("error", "failed", "failure")):
        return "error"
    if any(token in markers for token in ("final", "completed", "complete", "finished", "done", "result")):
        return "final"
    if any(token in markers for token in ("started", "start", "begin", "created")):
        return "started"
    if any(token in markers for token in ("progress", "status", "step", "delta", "message", "item", "turn", "exec", "output", "patch")):
        return "progress"
    return "unknown"


def _string_markers(payload: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("type", "event", "kind", "event_type", "name", "status"):
        value = payload.get(key)
        if isinstance(value, str):
            values.append(value)
    item = payload.get("item")
    if isinstance(item, dict):
        for key in ("type", "event", "kind", "name", "status"):
            value = item.get(key)
            if isinstance(value, str):
                values.append(value)
    return " ".join(values).lower()


def _extract_summary(payload: dict[str, Any], category: str) -> str:
    fallback = _EVENT_FALLBACKS.get(category, _EVENT_FALLBACKS["unknown"])
    for key in ("summary", "message", "status", "title"):
        value = payload.get(key)
        if isinstance(value, str):
            summary = sanitized_codex_text(value, fallback)
            if summary != fallback:
                return summary
    return fallback


def _event_timestamp(payload: dict[str, Any]) -> float | None:
    for key in ("timestamp", "created_at", "time"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            if timestamp > 0:
                return timestamp
    return None


def _has_permission_marker(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if (
                any(marker in key_text for marker in ("permission", "approval", "escalation"))
                and any(marker in key_text for marker in ("request", "required", "ask", "grant", "allow"))
            ):
                return True
            if _has_permission_marker(item):
                return True
    elif isinstance(value, list):
        return any(_has_permission_marker(item) for item in value)
    elif isinstance(value, str):
        text = value.lower()
        return any(marker in text for marker in ("permission", "approval", "escalation")) and any(
            marker in text for marker in ("request", "required", "approve", "allow", "grant")
        )
    return False


def _permission_request(payload: dict[str, Any]) -> dict[str, Any]:
    permission_hash = _permission_hash(payload)
    resume_token = _safe_resume_token(_find_first(payload, ("resume_token", "continuation_token", "resume_id", "request_id", "id")), permission_hash)
    return {
        "resumable": _coerce_bool(_find_first(payload, ("resume_supported", "resumable", "can_resume", "supports_resume", "resume"))),
        "resume_token": resume_token,
        "permission_hash": permission_hash,
        "tool": _safe_permission_tool(_find_first(payload, ("tool", "tool_name", "action", "command", "cmd"))),
    }


def _permission_hash(payload: dict[str, Any]) -> str:
    try:
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        serialized = repr(payload)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _safe_resume_token(value: object, fallback_hash: str) -> str:
    token = str(value or "").strip()
    if not token:
        return fallback_hash
    if len(token) > 160 or _UNSAFE_RE.search(token):
        return fallback_hash
    return token


def _safe_permission_tool(value: object) -> str:
    text = str(value or "").lower()
    if "shell" in text or "command" in text or "cmd" in text:
        return "command"
    if "file" in text:
        return "file"
    if "network" in text or "http" in text:
        return "network"
    return "external_permission"


def _find_first(value: object, names: tuple[str, ...]) -> object:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in names:
                return item
            found = _find_first(item, names)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first(item, names)
            if found is not None:
                return found
    return None


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "supported", "resumable"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _has_content(path: Path) -> bool:
    try:
        return path.is_file() and bool(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False
