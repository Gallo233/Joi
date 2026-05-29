from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import shutil
import sys
from typing import Any

from agent_companion.core.speech_input import AsrRuntimeState


SKILL_MANIFEST_VERSION = "joi.skill_manifest.v1"


@dataclass(frozen=True)
class NativeSkillManifest:
    id: str
    label: str
    category: str
    description: str
    tools: tuple[str, ...] = ()
    rpc_methods: tuple[str, ...] = ()
    input_schema: dict[str, Any] = field(default_factory=dict)
    result_schema: dict[str, Any] = field(default_factory=dict)
    permission_level: str = "low"
    supports_dry_run: bool = False
    local_capability: str = "ready"
    enabled: bool = True
    configured: bool = True
    state_policy: str = "ephemeral"
    audit: str = "event_log"
    notes: tuple[str, ...] = ()

    def to_agent_state(self) -> dict[str, Any]:
        return {
            "id": _safe_token(self.id),
            "label": self.label[:64],
            "category": _safe_token(self.category),
            "description": self.description[:180],
            "tools": [_safe_tool_name(tool) for tool in self.tools],
            "rpc_methods": [_safe_tool_name(method) for method in self.rpc_methods],
            "input_schema": _safe_schema(self.input_schema),
            "result_schema": _safe_schema(self.result_schema),
            "permission_level": _safe_permission(self.permission_level),
            "supports_dry_run": bool(self.supports_dry_run),
            "local_capability": _safe_capability(self.local_capability),
            "enabled": bool(self.enabled),
            "configured": bool(self.configured),
            "state_policy": _safe_token(self.state_policy),
            "audit": _safe_token(self.audit),
            "notes": [_safe_note(note) for note in self.notes if _safe_note(note)],
        }


def build_native_skill_manifest(
    workspace: Path,
    *,
    asr_state: AsrRuntimeState | None = None,
    tts_status: dict[str, Any] | None = None,
    memory_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tts_status = tts_status or {}
    memory_status = memory_status or {}
    skills = [
        _companion_chat_skill(),
        _codex_skill(),
        _browser_skill(),
        _computer_use_skill(),
        _watch_skill(),
        _memory_skill(memory_status),
        _voice_input_skill(asr_state),
        _voice_output_skill(tts_status),
        _ok_ww_skill(),
    ]
    return {
        "version": SKILL_MANIFEST_VERSION,
        "safe_for_display": True,
        "workspace_bound": True,
        "skills": [skill.to_agent_state() for skill in skills],
    }


def _companion_chat_skill() -> NativeSkillManifest:
    return NativeSkillManifest(
        id="joi.companion.chat",
        label="Companion Chat",
        category="companion",
        description="Character-safe conversation with emotion and memory context.",
        tools=("companion.chat",),
        input_schema=_object_schema("text", "memory_context"),
        result_schema=_tool_result_schema("reply", "expression_sync", "model_usage"),
        permission_level="low",
        state_policy="session",
    )


def _codex_skill() -> NativeSkillManifest:
    available = _codex_available()
    return NativeSkillManifest(
        id="joi.codex",
        label="Codex Coding",
        category="coding",
        description="Local coding-task execution through Codex with sanitized audit output.",
        tools=("codex.run",),
        input_schema=_object_schema("goal"),
        result_schema=_tool_result_schema("codex_run"),
        permission_level="medium",
        supports_dry_run=False,
        local_capability="ready" if available else "unavailable",
        configured=available,
        state_policy="workspace_audit",
        audit="codex_run_audit",
        notes=("approval_gated", "workspace_bound"),
    )


def _browser_skill() -> NativeSkillManifest:
    return NativeSkillManifest(
        id="joi.browser",
        label="Browser",
        category="computer_use",
        description="Queued browser search and observe requests for page-level assistance.",
        tools=("browser.search", "browser.observe"),
        input_schema=_object_schema("query", "url"),
        result_schema=_tool_result_schema("queued"),
        permission_level="low",
        state_policy="session",
        notes=("bridge_required",),
    )


def _computer_use_skill() -> NativeSkillManifest:
    windows = sys.platform == "win32"
    return NativeSkillManifest(
        id="joi.computer_use",
        label="Computer Use",
        category="computer_use",
        description="Approval-gated screen observation, target grounding, and desktop actions.",
        tools=(
            "observe.screen",
            "vision.resolve_target",
            "vision.select_target",
            "computer.click",
            "computer.type_text",
            "computer.scroll",
            "computer.hotkey",
            "computer.workflow",
        ),
        input_schema=_object_schema("query", "target", "action"),
        result_schema=_tool_result_schema("computer_use_audit", "artifacts"),
        permission_level="medium",
        local_capability="ready" if windows else "unavailable",
        configured=windows,
        state_policy="audited_session",
        audit="computer_use_audit",
        notes=("approval_gated", "post_action_verification"),
    )


def _watch_skill() -> NativeSkillManifest:
    return NativeSkillManifest(
        id="joi.watch",
        label="Watch Together",
        category="watch",
        description="Session-scoped visual and transcript context for current media or page.",
        tools=("observe.screen", "watch.recall"),
        rpc_methods=("watch.loop.start", "watch.loop.stop", "watch.loop.configure", "watch.loop.refresh", "watch.loop.status"),
        input_schema=_object_schema("query", "transcript_source", "sample_count"),
        result_schema=_tool_result_schema("watch_context", "transcript", "model_usage"),
        permission_level="low",
        state_policy="session_window",
        notes=("user_started_loop", "no_video_recording_by_default"),
    )


def _memory_skill(memory_status: dict[str, Any]) -> NativeSkillManifest:
    enabled = bool(memory_status.get("enabled", True))
    return NativeSkillManifest(
        id="joi.memory",
        label="Memory",
        category="memory",
        description="Explicitly approved local memory candidates and semantic recall.",
        rpc_methods=(
            "memory.status",
            "memory.recall",
            "memory.browse_vault",
            "memory.save_candidate",
            "memory.reject_candidate",
            "memory.set_enabled",
            "memory.delete",
            "memory.clear",
        ),
        input_schema=_object_schema("query", "candidate_id", "memory_id"),
        result_schema=_tool_result_schema("memory", "memories", "vault"),
        permission_level="low",
        local_capability="ready" if enabled else "off",
        enabled=enabled,
        configured=True,
        state_policy="explicit_local_vault",
        audit="memory_candidate_log",
        notes=("explicit_approval_required", "sensitive_rejected_by_default"),
    )


def _voice_input_skill(asr_state: AsrRuntimeState | None) -> NativeSkillManifest:
    enabled = bool(asr_state.enabled) if asr_state else False
    configured = bool(asr_state.configured) if asr_state else False
    error = str(asr_state.error or "") if asr_state else "asr_unconfigured"
    return NativeSkillManifest(
        id="joi.voice_input",
        label="Voice Input",
        category="voice",
        description="Click-to-record ASR routed into normal user-message handling.",
        rpc_methods=("voice.transcribe", "audio.transcribe"),
        input_schema=_object_schema("audio_base64", "mime_type"),
        result_schema=_tool_result_schema("transcript", "events"),
        permission_level="low",
        local_capability="ready" if configured else ("off" if not enabled else "unavailable"),
        enabled=enabled,
        configured=configured,
        state_policy="ephemeral_audio",
        audit="voice_event",
        notes=tuple(note for note in ("size_limited", _safe_note(error)) if note),
    )


def _voice_output_skill(tts_status: dict[str, Any]) -> NativeSkillManifest:
    enabled = bool(tts_status.get("enabled", False))
    configured = bool(tts_status.get("configured", False))
    error = _safe_note(str(tts_status.get("last_error") or ""))
    return NativeSkillManifest(
        id="joi.voice_output",
        label="Voice Output",
        category="voice",
        description="TTS synthesis for safe Joi voice lines and expression sync.",
        input_schema=_object_schema("voice_text", "sprite", "emotion"),
        result_schema=_tool_result_schema("voice_audio_data_url", "voice_audio_error"),
        permission_level="low",
        local_capability="ready" if configured else ("off" if not enabled else "unavailable"),
        enabled=enabled,
        configured=configured,
        state_policy="ephemeral_audio",
        audit="voice_audio_event",
        notes=tuple(note for note in ("safe_voice_only", error) if note),
    )


def _ok_ww_skill() -> NativeSkillManifest:
    available = _ok_ww_available()
    return NativeSkillManifest(
        id="joi.ok_ww",
        label="OK-WW",
        category="game",
        description="Approval-gated Wuthering Waves automation handoff through the local runner.",
        tools=("game.ok_ww.run",),
        input_schema=_object_schema("intent", "dry_run"),
        result_schema=_tool_result_schema("dry_run", "returncode"),
        permission_level="medium",
        supports_dry_run=True,
        local_capability="ready" if available else "unavailable",
        configured=available,
        state_policy="external_game_runner",
        audit="game_run_log",
        notes=("dry_run_first", "approval_gated"),
    )


def _codex_available() -> bool:
    override = os.environ.get("AGENT_COMPANION_CODEX_BIN", "").strip()
    if override:
        return Path(override).is_file()
    return bool(shutil.which("codex"))


def _ok_ww_available() -> bool:
    script = os.environ.get("OK_WW_RUNNER", r"C:\Users\liujialuo\.codex\skills\github_issue_solver\scripts\run_ok_ww.ps1")
    return Path(script).is_file()


def _object_schema(*properties: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: {"type": "value"} for name in properties if name},
    }


def _tool_result_schema(*state_keys: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["ok", "display_card", "voice_line", "agent_state"],
        "agent_state_keys": [key for key in state_keys if key],
    }


def _safe_schema(value: dict[str, Any]) -> dict[str, Any]:
    encoded = str(value)
    if any(fragment in encoded for fragment in ("sk-", "api_key", "base_url", "C:\\", "/Users/", "http://", "https://")):
        return {"type": "object"}
    return value


def _safe_tool_name(value: str) -> str:
    text = str(value or "").strip()
    return text if text and all(char.isalnum() or char in "._-" for char in text) else "redacted"


def _safe_permission(value: str) -> str:
    text = _safe_token(value)
    return text if text in {"low", "medium", "high"} else "medium"


def _safe_capability(value: str) -> str:
    text = _safe_token(value)
    return text if text in {"ready", "off", "unavailable", "degraded"} else "unavailable"


def _safe_token(value: str) -> str:
    text = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if any(fragment in text for fragment in ("sk_", "token", "secret", "api_key", "key=", ":\\")):
        return "redacted"
    return "".join(char for char in text if char.isalnum() or char in "._")[:80] or "unknown"


def _safe_note(value: str) -> str:
    text = _safe_token(value)
    if text in {"unknown", "none"}:
        return ""
    return text
