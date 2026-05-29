from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


AGENT_STATE_BUDGET = 2000

_STRIP_KEYS = {
    "screenshot_path",
    "screenshot_rel",
    "artifact",
    "artifacts",
    "raw_log",
    "stderr",
    "stdout",
    "jsonl_path",
    "log_path",
    "audio_path",
    "voice_audio_path",
    "image_data",
    "image_data_url",
    "audio_base64",
    "base64",
    "raw_html",
    "raw_response",
    "arguments_hash",
    "approval_id",
    "selection_id",
}

_COORDINATE_KEYS = {
    "x",
    "y",
    "end_x",
    "end_y",
    "bbox",
    "bounds",
    "screen_bbox",
    "capture_rect",
    "rect",
    "preview",
    "width",
    "height",
}

_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\|/(?:Users|home|private|tmp|var|Volumes)/|\\\\|data/agent_companion/|"
    r"\.(?:png|jpg|jpeg|webp|gif|bmp|ppm|json|jsonl|log|txt|ya?ml|sqlite3?|db)\b)",
    re.IGNORECASE,
)
_LOCAL_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/(?:Users|home|private|tmp|var|Volumes)/|\\\\)", re.IGNORECASE)
_SECRET_RE = re.compile(r"(?:\bsk-[A-Za-z0-9_-]{6,}\b|\b(?:api[_-]?key|token|secret|password|bearer)\b)", re.IGNORECASE)
_INTERNAL_ID_RE = re.compile(r"\b(?:task|approval|selection|codex|run|resume)[-_]?[0-9a-f]{6,}\b", re.IGNORECASE)
_JSON_BLOCK_RE = re.compile(r"[\[{][\s\S]{80,}[\]}]")
_COORD_RE = re.compile(r"\b\d{1,5}\s*,\s*\d{1,5}\b")

_UI_STRIP_KEYS = {
    "screenshot_path",
    "raw_log",
    "stderr",
    "stdout",
    "jsonl_path",
    "log_path",
    "audio_path",
    "voice_audio_path",
    "image_data",
    "image_data_url",
    "audio_base64",
    "base64",
    "raw_html",
    "raw_response",
}


@dataclass(frozen=True)
class CompressedToolResult:
    agent_state: dict[str, Any]
    display_card: dict[str, Any]
    voice_line: str
    memory_candidate: dict[str, str] | None
    audit_log: dict[str, Any]
    original_size: int
    compressed_size: int

    @property
    def compression_ratio(self) -> float:
        if self.original_size <= 0:
            return 1.0
        return self.compressed_size / self.original_size

    def to_agent_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "planner_state": self.agent_state,
            "display_card": self.display_card,
            "voice_line": self.voice_line,
            "audit_log": self.audit_log,
            "original_size": self.original_size,
            "compressed_size": self.compressed_size,
            "compression_ratio": round(self.compression_ratio, 3),
        }
        if self.memory_candidate:
            payload["memory_candidate"] = self.memory_candidate
        return payload


def compress_tool_result(result: Any) -> CompressedToolResult:
    state = getattr(result, "agent_state", {}) or {}
    if not isinstance(state, dict):
        state = {}
    card = getattr(result, "display_card", None)
    voice = getattr(result, "voice_line", None)
    original_size = _estimate_size(state)
    planner_state = _compress_state(state)
    if _estimate_size(planner_state) > AGENT_STATE_BUDGET:
        planner_state = _compress_state_aggressively(planner_state)
    compressed_size = _estimate_size(planner_state)
    return CompressedToolResult(
        agent_state=planner_state,
        display_card=_display_card_channel(card),
        voice_line=_clean_voice_text(str(getattr(voice, "text", "") or "")),
        memory_candidate=_explicit_memory_candidate(state),
        audit_log=_audit_channel(result, state),
        original_size=original_size,
        compressed_size=compressed_size,
    )


def build_event_agent_state(result: Any) -> dict[str, Any]:
    """Build the UI-safe event channel and attach compressed planner channels."""
    state = getattr(result, "agent_state", {}) or {}
    if not isinstance(state, dict):
        state = {}
    event_state = _sanitize_ui_state(state)
    compressed = compress_tool_result(result)
    event_state["joi_juice"] = compressed.to_agent_state()
    event_state["result_channels"] = {
        "ui": "agent_state",
        "planner": "joi_juice.planner_state",
        "memory": "joi_juice.memory_candidate",
        "audit": "joi_juice.audit_log",
    }
    return event_state


def _compress_state(state: dict[str, Any]) -> dict[str, Any]:
    compressed: dict[str, Any] = {}
    for key, value in state.items():
        if key in _STRIP_KEYS or key in _COORDINATE_KEYS or key.startswith("audit_"):
            continue
        if key in {"target_candidate"} and isinstance(value, dict):
            compressed[key] = _candidate_summary(value)
            continue
        if key in {"target_candidates"} and isinstance(value, list):
            compressed[key] = [_candidate_summary(item) for item in value[:5] if isinstance(item, dict)]
            if len(value) > 5:
                compressed["target_candidates_more"] = len(value) - 5
            continue
        if key in {"observation", "computer_observation", "first_computer_observation"} and isinstance(value, dict):
            compressed[key] = _observation_summary(value)
            continue
        if key in {"ocr", "transcript"} and isinstance(value, dict):
            compressed[key] = _status_summary(value)
            continue
        cleaned = _compress_value(value)
        if cleaned not in ({}, [], ""):
            compressed[key] = cleaned
    return compressed


def _sanitize_ui_state(state: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in state.items():
        key_text = str(key)
        if key_text in _UI_STRIP_KEYS or key_text.startswith("debug_"):
            continue
        cleaned = _sanitize_ui_value(value)
        if cleaned not in ({}, [], ""):
            output[key_text] = cleaned
    return output


def _sanitize_ui_value(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in _UI_STRIP_KEYS or key_text.startswith("debug_"):
                continue
            cleaned = _sanitize_ui_value(item)
            if cleaned not in ({}, [], ""):
                output[key_text] = cleaned
        return output
    if isinstance(value, list):
        return [_sanitize_ui_value(item) for item in value]
    if isinstance(value, str):
        return _clean_ui_text(value)
    return value


def _compress_value(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in _STRIP_KEYS or key_text in _COORDINATE_KEYS:
                continue
            cleaned = _compress_value(item)
            if cleaned not in ({}, [], ""):
                output[key_text] = cleaned
        return output
    if isinstance(value, list):
        return [_compress_value(item) for item in value[:5]]
    if isinstance(value, str):
        return _clean_state_text(value)
    return value


def _compress_state_aggressively(state: dict[str, Any]) -> dict[str, Any]:
    keep = {"tool", "ok", "error", "needs_clarification", "candidate_selection_required", "target_candidate", "target_candidates"}
    return {key: _compress_value(value) for key, value in state.items() if key in keep}


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _clean_state_text(str(candidate.get(key) or ""))
        for key in ("label", "source", "region_name", "ambiguity", "reason", "role")
        if str(candidate.get(key) or "").strip()
    } | {
        "rank": int(candidate.get("rank") or 0),
        "confidence": round(float(candidate.get("confidence") or 0), 3),
        "clickable": bool(candidate.get("clickable")) if "clickable" in candidate else False,
        "enabled": candidate.get("enabled") is not False,
    }


def _observation_summary(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _clean_state_text(str(observation.get(key) or ""))
        for key in ("target", "status", "source", "active_window_title")
        if str(observation.get(key) or "").strip() and not _unsafe_text(str(observation.get(key) or ""))
    }


def _status_summary(value: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("status", "source", "error", "provider"):
        text = str(value.get(key) or "").strip()
        if text and not _unsafe_text(text):
            summary[key] = _clean_state_text(text)
    for key in ("segments", "text_blocks", "ocr_regions"):
        if isinstance(value.get(key), list):
            summary[f"{key}_count"] = len(value[key])
    return summary


def _display_card_channel(card: Any) -> dict[str, Any]:
    if card is None:
        return {}
    artifacts = getattr(card, "artifacts", []) or []
    return {
        "title": _clean_state_text(str(getattr(card, "title", "") or ""))[:120],
        "summary": _clean_state_text(str(getattr(card, "summary", "") or ""))[:240],
        "body": _clean_state_text(str(getattr(card, "body", "") or ""))[:600],
        "status": _clean_state_text(str(getattr(card, "status", "") or ""))[:40],
        "artifact_count": len(artifacts) if isinstance(artifacts, list) else 0,
    }


def _audit_channel(result: Any, state: dict[str, Any]) -> dict[str, Any]:
    risk = getattr(result, "risk", "")
    return {
        "tool": _clean_state_text(str(state.get("tool") or ""))[:80],
        "ok": bool(getattr(result, "ok", False)),
        "risk": str(getattr(risk, "value", risk) or "low")[:40],
        "original_keys": len(state),
    }


def _explicit_memory_candidate(state: dict[str, Any]) -> dict[str, str] | None:
    raw = state.get("memory_candidate")
    if not isinstance(raw, dict):
        return None
    text = _clean_state_text(str(raw.get("text") or raw.get("fact") or ""))
    if not text or _unsafe_text(text):
        return None
    return {
        "kind": _safe_label(str(raw.get("kind") or "note"), "note"),
        "text": text[:400],
        "source": _safe_label(str(raw.get("source") or "tool"), "tool"),
    }


def _clean_voice_text(text: str) -> str:
    value = text or ""
    for pattern in (_SECRET_RE, _PATH_RE, _INTERNAL_ID_RE, _JSON_BLOCK_RE, _COORD_RE):
        value = pattern.sub("", value)
    return " ".join(value.split()).strip()[:200]


def _clean_state_text(text: str) -> str:
    value = " ".join((text or "").split()).strip()
    if _unsafe_text(value):
        return ""
    if len(value) > 500:
        return value[:500] + f"...({len(value)} chars)"
    return value


def _clean_ui_text(text: str) -> str:
    value = str(text or "")
    if not value:
        return ""
    if value.startswith("data:"):
        return "[redacted-data-url]"
    value = _SECRET_RE.sub("[redacted]", value)
    value = _LOCAL_PATH_RE.sub("[local-path]", value)
    return value[:2000] + (f"...({len(value)} chars)" if len(value) > 2000 else "")


def _unsafe_text(text: str) -> bool:
    return bool(_SECRET_RE.search(text) or _PATH_RE.search(text) or _INTERNAL_ID_RE.search(text))


def _safe_label(value: str, fallback: str) -> str:
    text = (value or "").strip().casefold().replace("-", "_")
    return text[:40] if text.replace("_", "").isalnum() else fallback


def _estimate_size(data: Any) -> int:
    try:
        return len(json.dumps(data, ensure_ascii=False, default=str))
    except Exception:
        return len(str(data))
