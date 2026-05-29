from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agent_companion.core.computer_use.audit import COMPUTER_AUDIT_STATE_KEY
from agent_companion.core.schemas import AgentEvent, EventType


AUDIT_SCHEMA_VERSION = "joi.audit.v1"

_AUDITED_EVENT_TYPES = {
    EventType.APPROVAL_REQUIRED,
    EventType.AUDIT_EVENT,
    EventType.TOOL_STARTED,
    EventType.TOOL_COMPLETED,
    EventType.TOOL_FAILED,
    EventType.TASK_COMPLETED,
    EventType.TASK_FAILED,
}
_UNSAFE_TEXT_RE = re.compile(
    r"(?:[A-Za-z]:\\|/(?:Users|home|private|tmp|var|Volumes)/|\\\\|data/agent_companion/|"
    r"https?://|www\.|\bsk-[A-Za-z0-9_-]{6,}\b|\b(?:api[_-]?key|token|secret|password|bearer)\b|"
    r"\b(?:task|approval|selection|codex|run|resume)[-_]?[0-9a-f]{6,}\b|"
    r"\.(?:png|jpg|jpeg|webp|gif|bmp|ppm|json|jsonl|log|txt|ya?ml|sqlite3?|db)\b)",
    re.IGNORECASE,
)
_SAFE_KEY_RE = re.compile(r"[^a-z0-9_.-]+", re.IGNORECASE)


class AuditStore:
    def __init__(self, path: Path, *, max_records: int = 2000) -> None:
        self.path = path
        self.max_records = max(100, int(max_records or 2000))

    def record_event(self, event: AgentEvent) -> None:
        if event.type not in _AUDITED_EVENT_TYPES:
            return
        record = audit_record_from_event(event)
        if not record:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._trim_if_large()

    def recent(self, limit: int = 50) -> dict[str, Any]:
        rows = self._read_records()
        safe_limit = max(1, min(int(limit or 50), 200))
        return {
            "version": AUDIT_SCHEMA_VERSION,
            "safe_for_display": True,
            "record_count": len(rows),
            "records": rows[-safe_limit:],
        }

    def status(self) -> dict[str, Any]:
        return {
            "version": AUDIT_SCHEMA_VERSION,
            "safe_for_display": True,
            "record_count": len(self._read_records()),
            "storage": "local_jsonl",
        }

    def _read_records(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("version") == AUDIT_SCHEMA_VERSION:
                records.append(row)
        return records

    def _trim_if_large(self) -> None:
        try:
            if self.path.stat().st_size < 2_000_000:
                return
            rows = self._read_records()[-self.max_records :]
            with self.path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            return


def audit_record_from_event(event: AgentEvent) -> dict[str, Any]:
    state = event.agent_state if isinstance(event.agent_state, dict) else {}
    tool = _safe_identifier(state.get("tool") or state.get("event_tool"))
    skill = state.get("skill") if isinstance(state.get("skill"), dict) else {}
    skill_id = _safe_identifier(state.get("skill_id") or skill.get("skill_id"))
    risk = _safe_label(state.get("risk") or state.get("skill_permission_level"), "low")
    record: dict[str, Any] = {
        "version": AUDIT_SCHEMA_VERSION,
        "created_at": float(event.created_at or 0),
        "task_ref": _task_ref(event.task_id),
        "event_type": event.type.value,
        "outcome": _outcome(event),
        "title": _clean_text(getattr(event.display_card, "title", "") or "", "event"),
        "summary": _clean_text(getattr(event.display_card, "summary", "") or "", "Event recorded."),
        "status": _safe_label(getattr(event.display_card, "status", "") or "", "info"),
        "artifact_count": len(getattr(event.display_card, "artifacts", []) or []),
    }
    if tool:
        record["tool"] = tool
    if skill_id:
        record["skill_id"] = skill_id
    skill_category = _safe_label(state.get("skill_category"), "")
    if skill_category:
        record["skill_category"] = skill_category
    audit_policy = _safe_label(state.get("skill_audit"), "")
    if audit_policy:
        record["audit_policy"] = audit_policy
    if risk:
        record["risk"] = risk
    block_reason = _safe_label(state.get("block_reason"), "")
    if block_reason:
        record["block_reason"] = block_reason
    approval = _approval_state(state)
    if approval:
        record["approval"] = approval
    policy = _policy_state(state)
    if policy:
        record["policy"] = policy
    computer_audit = _computer_audit_rows(state)
    if computer_audit:
        record["computer_audit"] = computer_audit
    return _drop_empty(record)


def _outcome(event: AgentEvent) -> str:
    state = event.agent_state if isinstance(event.agent_state, dict) else {}
    if bool(state.get("blocked")):
        return "blocked"
    if event.type == EventType.APPROVAL_REQUIRED:
        return "approval_pending"
    if bool(state.get("cancelled")):
        return "cancelled"
    if bool(state.get("approval_expired")):
        return "expired"
    if event.type in {EventType.TOOL_COMPLETED, EventType.TASK_COMPLETED}:
        return "success"
    if event.type in {EventType.TOOL_FAILED, EventType.TASK_FAILED}:
        return "failed"
    if event.type == EventType.TOOL_STARTED:
        return "started"
    return "recorded"


def _approval_state(state: dict[str, Any]) -> dict[str, Any]:
    approval = state.get("approval") if isinstance(state.get("approval"), dict) else {}
    if approval:
        return {
            "status": "pending",
            "tool": _safe_identifier(approval.get("tool")),
            "step_index": _safe_int(approval.get("step_index")),
        }
    if state.get("cancelled"):
        return {"status": "denied"}
    if state.get("approval_expired"):
        return {"status": "expired"}
    if state.get("approval_mismatch"):
        return {"status": "mismatch"}
    return {}


def _policy_state(state: dict[str, Any]) -> dict[str, Any]:
    policy = state.get("policy") if isinstance(state.get("policy"), dict) else {}
    if not policy:
        return {}
    return {
        "tool": _safe_identifier(policy.get("tool")),
        "reason": _clean_text(str(policy.get("reason") or ""), ""),
        "arguments_preview": _safe_mapping(policy.get("arguments_preview")),
    }


def _computer_audit_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = state.get(COMPUTER_AUDIT_STATE_KEY)
    if not isinstance(rows, list):
        return []
    return [_safe_computer_audit(row) for row in rows[:12] if isinstance(row, dict)]


def _safe_computer_audit(row: dict[str, Any]) -> dict[str, Any]:
    before = row.get("before_artifacts") if isinstance(row.get("before_artifacts"), list) else []
    after = row.get("after_artifacts") if isinstance(row.get("after_artifacts"), list) else []
    payload = {
        "event_type": _safe_label(row.get("event_type"), "audit"),
        "summary": _clean_text(str(row.get("sanitized_summary") or ""), "Audit event recorded."),
        "risk": _safe_label(row.get("risk_level"), "medium"),
        "approval_status": _safe_label(row.get("approval_status"), ""),
        "tool": _safe_identifier(row.get("tool_name")),
        "skill_id": _safe_identifier(row.get("skill_id")),
        "action": _safe_label(row.get("action_name"), ""),
        "arguments": _safe_mapping(row.get("sanitized_arguments")),
        "verification": _safe_verification(row.get("verification_result")),
        "candidate_evidence": _safe_evidence(row.get("candidate_evidence")),
        "artifact_counts": {"before": len(before), "after": len(after)},
    }
    return _drop_empty(payload)


def _safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    output: dict[str, Any] = {}
    for key, item in value.items():
        safe_key = _safe_label(key, "")
        if not safe_key:
            continue
        if isinstance(item, bool):
            output[safe_key] = item
        elif isinstance(item, int):
            output[safe_key] = _safe_int(item)
        elif isinstance(item, float):
            output[safe_key] = round(float(item), 3)
        else:
            text = _clean_text(str(item or ""), "")
            if text:
                output[safe_key] = text[:120]
    return output


def _safe_verification(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "status": _safe_label(value.get("status"), "unknown"),
        "summary": _clean_text(str(value.get("summary") or ""), ""),
        "signals": _safe_mapping(value.get("signals")),
    }


def _safe_evidence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value[:5]:
        if isinstance(item, dict):
            rows.append(_safe_mapping(item))
    return rows


def _clean_text(value: str, fallback: str) -> str:
    text = " ".join((value or "").split()).strip()
    if not text or _UNSAFE_TEXT_RE.search(text):
        return fallback
    return text[:240]


def _safe_identifier(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text or _UNSAFE_TEXT_RE.search(text):
        return ""
    text = _SAFE_KEY_RE.sub("", text)[:80]
    return text if text.replace(".", "").replace("_", "").replace("-", "").isalnum() else ""


def _safe_label(value: Any, fallback: str) -> str:
    text = _safe_identifier(value).replace(".", "_").replace("-", "_")
    return text[:60] if text else fallback


def _safe_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if -1000 <= number <= 1000 else 0


def _task_ref(task_id: str) -> str:
    return hashlib.sha256(str(task_id or "").encode("utf-8")).hexdigest()[:12]


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item not in ("", {}, [], None)
    }
