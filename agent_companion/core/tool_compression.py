"""P6 JoiJuice — Tool Result Compression.

Splits tool results into separate channels and compresses large
outputs before they reach the LLM context window.

Channels:
  - agent_state: compact data for LLM reasoning (compressed)
  - display_card: rich data for UI rendering (full)
  - voice_line: clean spoken text (no JSON/ids/paths)
  - memory_candidate: data worth remembering (filtered)
  - audit_log: full trace for developer inspection (full)

Inspired by AIRI's token compression approach.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


# Maximum characters for agent_state before compression
AGENT_STATE_BUDGET = 2000

# Patterns to strip from voice lines
VOICE_STRIP_PATTERNS = [
    re.compile(r'["\']'),
    re.compile(r'\b\w{20,}\b'),  # long tokens/hashes
    re.compile(r'/(?:Users|home|tmp|var|etc)/\S+'),  # file paths
    re.compile(r'[A-Z]:\\\\\S+'),  # Windows paths
    re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}\b'),  # IP addresses
    re.compile(r'sk-[A-Za-z0-9_-]{4,}'),  # API keys
    re.compile(r'\{[^}]{50,}\}'),  # long JSON blocks
    re.compile(r'\[[^\]]{100,}\]'),  # long arrays
]

# Patterns to strip from agent_state
STATE_STRIP_KEYS = frozenset({
    "screenshot_path", "screenshot_rel", "raw_log", "stderr",
    "stdout", "jsonl_path", "log_path", "audio_path",
    "image_data", "base64", "raw_html", "raw_response",
})


@dataclass(frozen=True)
class CompressedResult:
    """A tool result split into separate channels."""
    agent_state: dict[str, Any]       # Compact for LLM
    display_card: dict[str, Any]      # Full for UI
    voice_line: str                   # Clean for TTS
    memory_candidate: dict[str, Any] | None  # Worth remembering
    audit_log: dict[str, Any]         # Full for dev inspection
    original_tokens_estimate: int     # Estimated original size
    compressed_tokens_estimate: int   # Estimated compressed size

    @property
    def compression_ratio(self) -> float:
        if self.original_tokens_estimate <= 0:
            return 1.0
        return self.compressed_tokens_estimate / self.original_tokens_estimate


def compress_tool_result(result: Any) -> CompressedResult:
    """Compress a ToolResult into separate channels.

    Args:
        result: A ToolResult with agent_state, display_card, voice_line

    Returns:
        CompressedResult with split and compressed channels
    """
    agent_state = getattr(result, "agent_state", {}) or {}
    display_card = getattr(result, "display_card", None)
    voice_line_obj = getattr(result, "voice_line", None)
    risk = getattr(result, "risk", None)

    # Estimate original size
    original_size = _estimate_size(agent_state)

    # 1. Compress agent_state for LLM
    compressed_state = _compress_agent_state(agent_state)

    # 2. Extract display_card data (keep full)
    card_data = {}
    if display_card:
        card_data = {
            "title": getattr(display_card, "title", ""),
            "summary": getattr(display_card, "summary", ""),
            "body": _truncate(getattr(display_card, "body", ""), 1000),
            "status": getattr(display_card, "status", "info"),
            "artifacts": getattr(display_card, "artifacts", []),
        }

    # 3. Clean voice line
    voice_text = ""
    if voice_line_obj:
        voice_text = _clean_voice_line(getattr(voice_line_obj, "text", ""))

    # 4. Extract memory candidate
    memory_candidate = _extract_memory_candidate(agent_state)

    # 5. Build audit log (full data)
    audit = {
        "tool": agent_state.get("tool", ""),
        "ok": agent_state.get("ok", True),
        "risk": str(getattr(risk, "value", risk)) if risk else "low",
    }
    # Include audit-specific data
    if "computer_use" in agent_state:
        audit["computer_use"] = agent_state["computer_use"]
    if "post_action_verification" in agent_state:
        audit["verification"] = agent_state["post_action_verification"]
    if "codex_run" in agent_state:
        audit["codex_run"] = agent_state["codex_run"]

    compressed_size = _estimate_size(compressed_state)

    return CompressedResult(
        agent_state=compressed_state,
        display_card=card_data,
        voice_line=voice_text,
        memory_candidate=memory_candidate,
        audit_log=audit,
        original_tokens_estimate=original_size,
        compressed_tokens_estimate=compressed_size,
    )


def _compress_agent_state(state: dict[str, Any]) -> dict[str, Any]:
    """Compress agent_state for LLM consumption.

    - Remove large binary/path fields
    - Truncate long strings
    - Summarize lists
    - Remove redundant nested data
    - PRESERVE structured UI data (target candidates, evidence, approvals)
    """
    if not state:
        return {}

    # Keys that must survive compression intact (frontend/UI needs them)
    PRESERVE_KEYS = frozenset({
        "tool", "ok", "error", "needs_clarification", "coordinate_untrusted",
        "candidate_selection_required", "selection_id", "selected_rank",
        "target_candidate", "target_candidates", "approval_request",
        "observation", "ocr", "ocr_regions", "visual_detection",
        "computer_use", "verification", "post_action_verification",
        "codex_run", "memory_candidate", "subconscious", "tick_id",
        "_compression_ratio", "_original_size",
    })

    compressed: dict[str, Any] = {}
    for key, value in state.items():
        # Skip heavy fields
        if key in STATE_STRIP_KEYS:
            continue
        if key == "audit" or key.startswith("audit_"):
            continue

        # Preserve important UI fields as-is
        if key in PRESERVE_KEYS:
            compressed[key] = value
            continue

        # Compress nested dicts
        if isinstance(value, dict):
            nested = _compress_dict(value)
            if nested:
                compressed[key] = nested
        # Truncate long strings
        elif isinstance(value, str):
            if len(value) > 500:
                compressed[key] = value[:500] + f"...({len(value)} chars)"
            else:
                compressed[key] = value
        # Summarize long lists
        elif isinstance(value, list):
            if len(value) > 10:
                compressed[key] = value[:5] + [f"...{len(value) - 5} more items"]
            else:
                compressed[key] = value
        else:
            compressed[key] = value

    return compressed


def _compress_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively compress a nested dict."""
    result: dict[str, Any] = {}
    for k, v in d.items():
        if k in STATE_STRIP_KEYS:
            continue
        if isinstance(v, str) and len(v) > 300:
            result[k] = v[:300] + "..."
        elif isinstance(v, list) and len(v) > 5:
            result[k] = v[:3] + [f"...{len(v) - 3} more"]
        elif isinstance(v, dict):
            result[k] = _compress_dict(v)
        else:
            result[k] = v
    return result


def _aggressive_compress(state: dict[str, Any]) -> dict[str, Any]:
    """Aggressively compress when still over budget."""
    result: dict[str, Any] = {}
    for key, value in state.items():
        if isinstance(value, str):
            result[key] = value[:200]
        elif isinstance(value, dict):
            # Keep only top-level keys
            result[key] = {k: str(v)[:100] for k, v in list(value.items())[:5]}
        elif isinstance(value, list):
            result[key] = f"[{len(value)} items]"
        else:
            result[key] = value
    return result


def _extract_memory_candidate(state: dict[str, Any]) -> dict[str, Any] | None:
    """Extract memory-worthy data from agent_state."""
    candidate = state.get("memory_candidate")
    if isinstance(candidate, dict):
        return candidate

    # Auto-generate from tool results
    tool = state.get("tool", "")
    if not tool:
        return None

    # Only remember certain tool types
    memorable_tools = {"codex.run", "browser.search", "browser.observe", "observe.screen", "companion.chat"}
    if tool not in memorable_tools:
        return None

    summary = state.get("summary") or state.get("vision_summary") or ""
    if not summary or len(summary) < 10:
        return None

    return {
        "kind": "tool_result",
        "text": summary[:400],
        "source": tool,
    }


def _clean_voice_line(text: str) -> str:
    """Clean a voice line for TTS — remove technical noise."""
    if not text:
        return ""
    cleaned = text
    for pattern in VOICE_STRIP_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    # Collapse whitespace
    cleaned = " ".join(cleaned.split())
    return cleaned[:200]  # Hard cap for TTS


def _truncate(text: str, max_len: int) -> str:
    if not text or len(text) <= max_len:
        return text
    return text[:max_len] + f"...({len(text)} chars)"


def _estimate_size(data: Any) -> int:
    """Estimate the size of data in characters (rough token proxy)."""
    try:
        return len(json.dumps(data, ensure_ascii=False, default=str))
    except Exception:
        return len(str(data))
