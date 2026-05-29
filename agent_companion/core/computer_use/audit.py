from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time
from typing import Any

from agent_companion.core.schemas import RiskLevel, ToolRequest, ToolResult
from agent_companion.core.skill_manifest import skill_id_for_tool


COMPUTER_AUDIT_STATE_KEY = "computer_use_audit"


@dataclass(frozen=True)
class ComputerUseAuditArtifact:
    role: str
    kind: str
    label: str
    ref: str = ""

    def to_agent_state(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComputerUseAuditEvent:
    task_id: str
    event_type: str
    timestamp: float
    sanitized_summary: str
    risk_level: str = RiskLevel.LOW.value
    approval_id: str = ""
    approval_status: str = ""
    tool_name: str = ""
    skill_id: str = ""
    action_name: str = ""
    sanitized_arguments: dict[str, Any] = field(default_factory=dict)
    before_artifacts: list[ComputerUseAuditArtifact] = field(default_factory=list)
    after_artifacts: list[ComputerUseAuditArtifact] = field(default_factory=list)
    verification_result: dict[str, Any] = field(default_factory=dict)
    candidate_evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_agent_state(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["before_artifacts"] = [artifact.to_agent_state() for artifact in self.before_artifacts]
        payload["after_artifacts"] = [artifact.to_agent_state() for artifact in self.after_artifacts]
        return payload


def audit_state(entries: list[ComputerUseAuditEvent]) -> list[dict[str, Any]]:
    return [entry.to_agent_state() for entry in entries]


def computer_approval_audit_event(
    task_id: str,
    request: ToolRequest,
    risk: RiskLevel | str,
    approval_id: str,
    status: str,
    summary: str,
    artifacts: list[str] | None = None,
) -> ComputerUseAuditEvent:
    return ComputerUseAuditEvent(
        task_id=task_id,
        event_type=f"approval_{status}",
        timestamp=time.time(),
        sanitized_summary=sanitize_summary(summary),
        risk_level=_risk_value(risk),
        approval_id=approval_id,
        approval_status=status,
        tool_name=request.name,
        skill_id=skill_id_for_tool(request.name),
        action_name=_action_name(request.name),
        sanitized_arguments=sanitize_tool_arguments(request.name, request.arguments),
        before_artifacts=_artifact_list(artifacts or [], "before"),
    )


def computer_action_audit_event(task_id: str, result: ToolResult, risk: RiskLevel | str = RiskLevel.MEDIUM) -> ComputerUseAuditEvent | None:
    state = result.agent_state if isinstance(result.agent_state, dict) else {}
    computer_use = state.get("computer_use") if isinstance(state.get("computer_use"), dict) else {}
    action = computer_use.get("action") if isinstance(computer_use.get("action"), dict) else {}
    tool_name = str(state.get("tool") or "")
    action_type = str(action.get("type") or _action_name(tool_name) or "")
    if not tool_name.startswith("computer.") and not action_type:
        return None
    before_ref = str(computer_use.get("before_artifact") or "")
    after_ref = str(computer_use.get("after_artifact") or "")
    verification = state.get("post_action_verification") if isinstance(state.get("post_action_verification"), dict) else {}
    status = str(verification.get("status") or ("completed" if result.ok else "failed"))
    if status == "likely_noop":
        event_type = "verification_noop"
    elif status == "unavailable":
        event_type = "verification_unavailable"
    elif status == "inconclusive":
        event_type = "verification_inconclusive"
    else:
        event_type = "action_verified" if result.ok else "action_failed"
    return ComputerUseAuditEvent(
        task_id=task_id,
        event_type=event_type,
        timestamp=time.time(),
        sanitized_summary=sanitize_summary(result.display_card.summary or "Computer Use action recorded."),
        risk_level=_risk_value(risk),
        tool_name=tool_name,
        skill_id=skill_id_for_tool(tool_name),
        action_name=action_type,
        sanitized_arguments=sanitize_action_state(action),
        before_artifacts=_artifact_list([before_ref], "before"),
        after_artifacts=_artifact_list([after_ref], "after"),
        verification_result=sanitize_verification(verification),
    )


def target_grounding_audit_events(task_id: str, result: ToolResult, risk: RiskLevel | str = RiskLevel.LOW) -> list[ComputerUseAuditEvent]:
    state = result.agent_state if isinstance(result.agent_state, dict) else {}
    tool_name = str(state.get("tool") or "")
    if tool_name not in {"vision.resolve_target", "vision.select_target"}:
        return []
    entries: list[ComputerUseAuditEvent] = []
    artifacts = [str(item) for item in (state.get("artifacts") if isinstance(state.get("artifacts"), list) else result.display_card.artifacts or [])]
    observation = state.get("observation") if isinstance(state.get("observation"), dict) else {}
    if observation or artifacts:
        entries.append(
            ComputerUseAuditEvent(
                task_id=task_id,
                event_type="observe",
                timestamp=time.time(),
                sanitized_summary="Observed the active window for Computer Use grounding.",
                risk_level=_risk_value(risk),
                tool_name=tool_name,
                skill_id=skill_id_for_tool(tool_name),
                action_name="observe",
                sanitized_arguments={"target": "active_window"},
                before_artifacts=_artifact_list(artifacts, "before"),
            )
        )
    candidates = state.get("target_candidates") if isinstance(state.get("target_candidates"), list) else []
    candidate = state.get("target_candidate") if isinstance(state.get("target_candidate"), dict) else {}
    if candidates or candidate:
        entries.append(
            ComputerUseAuditEvent(
                task_id=task_id,
                event_type="target_candidates",
                timestamp=time.time(),
                sanitized_summary=_target_summary(state),
                risk_level=_risk_value(risk),
                tool_name=tool_name,
                skill_id=skill_id_for_tool(tool_name),
                action_name="target_candidate",
                sanitized_arguments=_target_arguments(state, candidates, candidate),
                before_artifacts=_artifact_list(artifacts, "before"),
                candidate_evidence=_target_evidence(candidates, candidate),
            )
        )
    if state.get("selection_expired") or state.get("selection_missing") or state.get("selection_not_current"):
        status = "expired" if state.get("selection_expired") else "missing" if state.get("selection_missing") else "not_current"
        entries.append(
            ComputerUseAuditEvent(
                task_id=task_id,
                event_type=f"target_selection_{status}",
                timestamp=time.time(),
                sanitized_summary="Candidate selection could not continue; a fresh observation is required.",
                risk_level=_risk_value(risk),
                tool_name=tool_name,
                skill_id=skill_id_for_tool(tool_name),
                action_name="target_selection",
                sanitized_arguments={"selection_status": status},
            )
        )
    return entries


def sanitize_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "computer.click":
        payload = {"target": "screen_position"}
        button = _safe_token(arguments.get("button"))
        if button:
            payload["button"] = button
        return payload
    if tool_name == "computer.type_text":
        return {"input": "typed_text_hidden", "characters": len(str(arguments.get("text") or ""))}
    if tool_name == "computer.scroll":
        direction = str(arguments.get("direction") or "").casefold()
        if not direction and "delta" in arguments:
            try:
                direction = "up" if int(arguments.get("delta") or 0) > 0 else "down"
            except (TypeError, ValueError):
                direction = "unknown"
        return {"direction": _safe_direction(direction), "amount": "scroll_steps_hidden"}
    if tool_name == "computer.hotkey":
        keys = arguments.get("keys")
        key_count = len(keys) if isinstance(keys, (list, tuple)) else 1 if keys else 0
        return {"shortcut": "keys_hidden", "key_count": key_count}
    if tool_name == "computer.workflow":
        workflow = _safe_token(arguments.get("workflow")) or "desktop_sequence"
        return {"workflow": workflow, "arguments": "hidden"}
    if tool_name in {"vision.resolve_target", "vision.select_target"}:
        return {"target_description": "hidden"}
    return {"arguments": "hidden"}


def sanitize_action_state(action: dict[str, Any]) -> dict[str, Any]:
    action_type = str(action.get("type") or "")
    if action_type == "click":
        return {"target": "screen_position", "button": _safe_token(action.get("button")) or "left"}
    if action_type == "type_text":
        return {"input": "typed_text_hidden", "characters": int(action.get("text_length") or 0)}
    if action_type == "scroll":
        try:
            direction = "up" if int(action.get("delta") or 0) > 0 else "down"
        except (TypeError, ValueError):
            direction = "unknown"
        return {"direction": direction, "amount": "scroll_steps_hidden"}
    if action_type == "hotkey":
        keys = action.get("keys")
        return {"shortcut": "keys_hidden", "key_count": len(keys) if isinstance(keys, list) else 0}
    if action_type == "workflow":
        workflow = _safe_token(action.get("workflow")) or "desktop_sequence"
        try:
            step_count = max(0, int(action.get("step_count") or 0))
        except (TypeError, ValueError):
            step_count = 0
        return {"workflow": workflow, "step_count": step_count}
    return {"action": action_type or "unknown"}


def sanitize_verification(verification: dict[str, Any]) -> dict[str, Any]:
    if not verification:
        return {"status": "unavailable", "summary": "Verification was not available."}
    signals = verification.get("signals") if isinstance(verification.get("signals"), dict) else {}
    return {
        "status": str(verification.get("status") or "unknown"),
        "summary": sanitize_summary(str(verification.get("summary") or "")),
        "signals": {str(key): _signal_label(value) for key, value in signals.items()},
    }


def sanitize_summary(text: str) -> str:
    value = " ".join((text or "").split()).strip()
    return value[:180] if value else "Computer Use audit event recorded."


def _artifact_list(refs: list[str], role: str) -> list[ComputerUseAuditArtifact]:
    rows: list[ComputerUseAuditArtifact] = []
    for ref in refs:
        value = str(ref or "").strip()
        if not value:
            continue
        kind = "screenshot" if value.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) else "artifact"
        label = "Before screenshot" if role == "before" and kind == "screenshot" else "After screenshot" if role == "after" and kind == "screenshot" else f"{role.title()} artifact"
        rows.append(ComputerUseAuditArtifact(role=role, kind=kind, label=label, ref=value))
    return rows


def _target_summary(state: dict[str, Any]) -> str:
    if state.get("candidate_selection_required"):
        return "Target candidates need explicit selection before any Computer Use action."
    if state.get("approval_request"):
        return "A target candidate was resolved and is waiting for Computer Use approval."
    if state.get("candidate_not_actionable"):
        return "The selected target is not actionable."
    return "Target candidates were recorded for review."


def _target_arguments(state: dict[str, Any], candidates: list[Any], candidate: dict[str, Any]) -> dict[str, Any]:
    top = candidate or (candidates[0] if candidates and isinstance(candidates[0], dict) else {})
    payload: dict[str, Any] = {
        "candidate_count": len(candidates) if candidates else 1 if top else 0,
        "requires_selection": bool(state.get("candidate_selection_required")),
        "requires_approval": bool(state.get("approval_request")),
    }
    for key in ("source", "ambiguity"):
        value = _safe_token(top.get(key)) if isinstance(top, dict) else ""
        if value:
            payload[key] = value
    rank = top.get("rank") if isinstance(top, dict) else None
    if isinstance(rank, int) and rank > 0:
        payload["rank"] = rank
    selected_rank = state.get("selected_rank")
    if isinstance(selected_rank, int) and selected_rank > 0:
        payload["selected_rank"] = selected_rank
    return payload


def _target_evidence(candidates: list[Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    rows = candidates if candidates else [candidate] if candidate else []
    evidence_rows: list[dict[str, Any]] = []
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        if not evidence:
            continue
        sanitized = {
            "rank": _safe_rank(evidence.get("rank") or row.get("rank")),
            "source": _safe_choice(evidence.get("source"), {"ocr", "accessibility", "visual", "fused", "unknown"}, "unknown"),
            "confidence_band": _safe_choice(evidence.get("confidence_band"), {"high", "medium", "low"}, "low"),
            "ambiguity_reason": _safe_choice(evidence.get("ambiguity_reason"), {"none", "close_score", "low_confidence"}, "none"),
            "actionability": _safe_choice(
                evidence.get("actionability"),
                {"actionable", "disabled", "static_text", "visual_only", "ocr_text", "ocr_uia_fused", "unknown"},
                "unknown",
            ),
            "capture_trust": _safe_choice(evidence.get("capture_trust"), {"trusted", "untrusted", "unavailable"}, "unavailable"),
        }
        evidence_rows.append(sanitized)
    return evidence_rows


def _safe_rank(value: Any) -> int:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return 0
    return rank if 0 <= rank <= 99 else 0


def _safe_choice(value: Any, allowed: set[str], fallback: str) -> str:
    text = str(value or "").strip().casefold()
    return text if text in allowed else fallback


def _action_name(tool_name: str) -> str:
    if tool_name.startswith("computer."):
        return tool_name.split(".", 1)[1]
    return tool_name


def _risk_value(risk: RiskLevel | str) -> str:
    return risk.value if isinstance(risk, RiskLevel) else str(risk or RiskLevel.LOW.value)


def _safe_token(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return text if text.replace("_", "").replace("-", "").isalnum() and len(text) <= 40 else ""


def _safe_direction(value: str) -> str:
    if value in {"up", "上", "向上"}:
        return "up"
    if value in {"down", "下", "向下"}:
        return "down"
    return "unknown"


def _signal_label(value: Any) -> str:
    if value is True:
        return "changed"
    if value is False:
        return "unchanged"
    return "unknown"
