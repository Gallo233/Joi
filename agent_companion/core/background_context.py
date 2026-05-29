from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any


BACKGROUND_CONTEXT_VERSION = "joi.background_context.v1"

_SCOPE_TYPES = {"window", "project", "game"}
_UNSAFE_TEXT_RE = re.compile(
    r"(?:[A-Za-z]:\\|/(?:Users|home|private|tmp|var|Volumes)/|\\\\|data/agent_companion/|"
    r"https?://|www\.|\bsk-[A-Za-z0-9_-]{6,}\b|\b(?:api[_-]?key|token|secret|password|bearer)\b|"
    r"\b(?:task|approval|selection|codex|run|resume)[-_]?[0-9a-f]{6,}\b|"
    r"\.(?:png|jpg|jpeg|webp|gif|bmp|ppm|json|jsonl|log|txt|ya?ml|sqlite3?|db)\b)",
    re.IGNORECASE,
)
_SAFE_TOKEN_RE = re.compile(r"[^a-z0-9_-]+", re.IGNORECASE)


class BackgroundContextStore:
    def __init__(self, path: Path, *, max_recent: int = 80) -> None:
        self.path = path
        self.max_recent = max(10, int(max_recent or 80))

    def status(self) -> dict[str, Any]:
        return _public_state(self._load())

    def configure(
        self,
        *,
        enabled: bool | None = None,
        scope_type: str = "",
        label: str = "",
        active_scope_id: str = "",
    ) -> dict[str, Any]:
        state = self._load()
        if enabled is not None:
            state["enabled"] = bool(enabled)
            if not enabled:
                state["active_scope_id"] = ""
        scope = _scope_from_input(scope_type, label)
        if scope:
            scopes = _scope_rows(state)
            existing = next((row for row in scopes if row.get("id") == scope["id"]), None)
            if existing:
                existing.update(scope)
                existing["approved_at"] = existing.get("approved_at") or scope["approved_at"]
            else:
                scopes.append(scope)
            state["scopes"] = scopes[-50:]
            state["enabled"] = True if enabled is None else bool(enabled)
            if state["enabled"]:
                state["active_scope_id"] = scope["id"]
        elif active_scope_id:
            normalized_scope_id = _safe_token(active_scope_id)
            if not any(row.get("id") == normalized_scope_id for row in _scope_rows(state)):
                return {"ok": False, "error": "unknown_background_scope", "background": _public_state(state)}
            state["active_scope_id"] = normalized_scope_id if state.get("enabled") else ""
        self._write(state)
        return {"ok": True, "background": _public_state(state)}

    def clear_context(self) -> dict[str, Any]:
        state = self._load()
        state["recent_context"] = []
        self._write(state)
        return {"ok": True, "background": _public_state(state)}

    def record_summary(
        self,
        summary: str,
        *,
        source: str = "watch_loop",
        scope_id: str = "",
        visual_status: str = "",
        transcript_source: str = "",
    ) -> dict[str, Any]:
        state = self._load()
        if not bool(state.get("enabled")):
            return {"ok": False, "error": "background_disabled", "background": _public_state(state)}
        active_scope = _active_scope(state, scope_id)
        if not active_scope:
            return {"ok": False, "error": "background_scope_required", "background": _public_state(state)}
        text = _clean_text(summary, "")
        if not text:
            return {"ok": False, "error": "empty_background_summary", "background": _public_state(state)}
        recent = _recent_rows(state)
        recent.append(
            {
                "created_at": time.time(),
                "scope_id": active_scope["id"],
                "scope_type": active_scope["type"],
                "source": _safe_source(source),
                "summary": text,
                "visual_status": _safe_token(visual_status),
                "transcript_source": _safe_token(transcript_source),
            }
        )
        state["recent_context"] = recent[-self.max_recent :]
        state["active_scope_id"] = active_scope["id"]
        self._write(state)
        return {"ok": True, "background": _public_state(state)}

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return _default_state()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _default_state()
        if not isinstance(raw, dict):
            return _default_state()
        state = _default_state()
        state["enabled"] = bool(raw.get("enabled", False))
        state["active_scope_id"] = _safe_token(raw.get("active_scope_id"))
        state["scopes"] = [_safe_scope_row(row) for row in raw.get("scopes", []) if isinstance(row, dict)]
        state["recent_context"] = [_safe_recent_row(row) for row in raw.get("recent_context", []) if isinstance(row, dict)]
        return state

    def _write(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = _storage_state(state)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


def _default_state() -> dict[str, Any]:
    return {
        "version": BACKGROUND_CONTEXT_VERSION,
        "enabled": False,
        "active_scope_id": "",
        "scopes": [],
        "recent_context": [],
    }


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    scopes = _scope_rows(state)
    active = _active_scope(state)
    recent = _recent_rows(state)
    return {
        "version": BACKGROUND_CONTEXT_VERSION,
        "safe_for_display": True,
        "enabled": bool(state.get("enabled")),
        "active": bool(state.get("enabled") and active),
        "active_scope": active or {},
        "approved_scopes": scopes,
        "scope_count": len(scopes),
        "recent_context": recent[-20:],
        "recent_count": len(recent),
        "retention": "summaries_only",
        "video_recording": False,
    }


def _storage_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": BACKGROUND_CONTEXT_VERSION,
        "enabled": bool(state.get("enabled")),
        "active_scope_id": _safe_token(state.get("active_scope_id")),
        "scopes": _scope_rows(state),
        "recent_context": _recent_rows(state),
    }


def _scope_from_input(scope_type: str, label: str) -> dict[str, Any]:
    scope = _safe_scope_type(scope_type)
    if not scope:
        return {}
    raw_label = str(label or scope).strip()
    safe_label = _clean_text(raw_label, f"approved_{scope}")
    return {
        "id": _scope_id(scope, raw_label),
        "type": scope,
        "label": safe_label,
        "approved_at": time.time(),
        "enabled": True,
    }


def _scope_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("scopes") if isinstance(state.get("scopes"), list) else []
    return [row for row in (_safe_scope_row(item) for item in rows if isinstance(item, dict)) if row]


def _recent_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("recent_context") if isinstance(state.get("recent_context"), list) else []
    return [row for row in (_safe_recent_row(item) for item in rows if isinstance(item, dict)) if row]


def _active_scope(state: dict[str, Any], override_scope_id: str = "") -> dict[str, Any]:
    scope_id = _safe_token(override_scope_id) or _safe_token(state.get("active_scope_id"))
    if not scope_id:
        return {}
    for row in _scope_rows(state):
        if row.get("id") == scope_id and row.get("enabled") is not False:
            return row
    return {}


def _safe_scope_row(row: dict[str, Any]) -> dict[str, Any]:
    scope = _safe_scope_type(str(row.get("type") or ""))
    scope_id = _safe_token(row.get("id"))
    if not scope or not scope_id:
        return {}
    return {
        "id": scope_id,
        "type": scope,
        "label": _clean_text(str(row.get("label") or ""), f"approved_{scope}"),
        "approved_at": _safe_time(row.get("approved_at")),
        "enabled": bool(row.get("enabled", True)),
    }


def _safe_recent_row(row: dict[str, Any]) -> dict[str, Any]:
    scope_id = _safe_token(row.get("scope_id"))
    summary = _clean_text(str(row.get("summary") or ""), "")
    if not scope_id or not summary:
        return {}
    return {
        "created_at": _safe_time(row.get("created_at")),
        "scope_id": scope_id,
        "scope_type": _safe_scope_type(str(row.get("scope_type") or "")) or "window",
        "source": _safe_source(row.get("source")),
        "summary": summary,
        "visual_status": _safe_token(row.get("visual_status")),
        "transcript_source": _safe_token(row.get("transcript_source")),
    }


def _scope_id(scope_type: str, label: str) -> str:
    digest = hashlib.sha256(f"{scope_type}:{label}".encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"scope-{digest}"


def _clean_text(value: str, fallback: str) -> str:
    text = " ".join((value or "").split()).strip()
    if not text or _UNSAFE_TEXT_RE.search(text):
        return fallback
    return text[:240]


def _safe_scope_type(value: str) -> str:
    text = str(value or "").strip().casefold().replace("-", "_")
    return text if text in _SCOPE_TYPES else ""


def _safe_source(value: Any) -> str:
    text = _safe_token(value)
    return text if text in {"watch_loop", "manual", "background_loop"} else "manual"


def _safe_token(value: Any) -> str:
    text = str(value or "").strip().casefold().replace(" ", "_")
    text = _SAFE_TOKEN_RE.sub("", text)[:80]
    return text if text.replace("_", "").replace("-", "").isalnum() else ""


def _safe_time(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if 0 <= number <= 4_102_444_800 else 0.0
