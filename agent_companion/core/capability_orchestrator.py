from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import time
from typing import Any, Protocol

from agent_companion.core.collaboration_store import CollaborationStore
from agent_companion.core.schemas import AgentEvent, EventType


PERCEPTION_PRIORITY = ("application_api_or_dom", "macos_accessibility", "ocr_or_vision", "coordinate_fallback")


class PerceptionDriver(Protocol):
    name: str
    priority: int

    def available(self) -> bool: ...


class ApplicationExecutor(Protocol):
    application_id: str

    def supports(self, action: str) -> bool: ...


@dataclass(frozen=True)
class DriverInventory:
    native_available: bool
    cua_available: bool
    selected: str
    perception_priority: tuple[str, ...] = PERCEPTION_PRIORITY
    notes: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "native_available": self.native_available,
            "cua_available": self.cua_available,
            "selected": self.selected,
            "perception_priority": list(self.perception_priority),
            "notes": list(self.notes),
        }


@dataclass
class SessionRuntime:
    session_id: str
    model_calls: int = 0
    pause_reason: str = ""
    repeated_noops: int = 0
    last_observation_signature: str = ""
    seen_signatures: list[str] = field(default_factory=list)


class ComputerUseOrchestrator:
    """Cross-app session guard around Joi's existing application executors.

    Existing tools remain responsible for a concrete action. This coordinator
    owns driver selection, budgets, receipts, no-op/loop detection and the
    pause boundary shared by native, Accessibility/vision and optional CUA.
    """

    def __init__(self, workspace: Path, store: CollaborationStore) -> None:
        self.workspace = workspace.resolve()
        self.store = store
        self._runtime: dict[str, SessionRuntime] = {}

    def driver_inventory(self, requested: str = "auto") -> DriverInventory:
        cua = _cua_available()
        selected = "cua" if requested in {"auto", "cua"} and cua else "native"
        notes: list[str] = []
        if requested == "cua" and not cua:
            notes.append("CUA 未安装，已降级到 Joi 原生前台驱动。")
        if selected == "native":
            notes.append("原生驱动可能短暂使用鼠标或焦点。")
        else:
            notes.append("CUA 后台驱动可在隔离环境中运行，不抢占前台焦点。")
        return DriverInventory(True, cua, selected, notes=tuple(notes))

    def preflight(self, session_id: str) -> dict[str, Any]:
        session = self.store.session_payload(session_id, include_receipts=False)
        if not session:
            return {"allowed": False, "reason": "session_not_found"}
        if session.get("state") != "running":
            return {"allowed": False, "reason": "session_not_running"}
        receipts = self.store.list_receipts(session_id, 200)
        budget = session.get("budget") if isinstance(session.get("budget"), dict) else {}
        elapsed = max(0.0, time.time() - float(session.get("created_at") or time.time()))
        runtime = self._runtime.setdefault(session_id, SessionRuntime(session_id))
        limits = {
            "steps": (len(receipts), int(budget.get("max_steps") or 30)),
            "seconds": (int(elapsed), int(budget.get("max_seconds") or 900)),
            "model_calls": (runtime.model_calls, int(budget.get("max_model_calls") or 40)),
            "failures": (_failure_count(receipts), int(budget.get("max_failures") or 3)),
        }
        exhausted = next((name for name, (used, maximum) in limits.items() if used >= maximum), "")
        if exhausted:
            reason = f"budget_exhausted:{exhausted}"
            self.pause(session_id, reason)
            return {"allowed": False, "reason": reason, "limits": limits}
        return {"allowed": True, "limits": limits, "driver": session.get("driver") or "native"}

    def record_model_call(self, session_id: str) -> dict[str, Any]:
        runtime = self._runtime.setdefault(session_id, SessionRuntime(session_id))
        runtime.model_calls += 1
        return self.preflight(session_id)

    def record_tool_event(self, session_id: str, event: AgentEvent) -> dict[str, Any]:
        state = event.agent_state if isinstance(event.agent_state, dict) else {}
        tool = str(state.get("tool") or "")
        if event.type not in {EventType.TOOL_COMPLETED, EventType.TOOL_FAILED}:
            return {"ok": False, "ignored": True}
        verification = state.get("post_action_verification") if isinstance(state.get("post_action_verification"), dict) else {}
        computer = state.get("computer_use") if isinstance(state.get("computer_use"), dict) else {}
        receipt = self.store.add_receipt(
            session_id,
            {
                "action": tool,
                "risk": str(state.get("risk") or state.get("skill_permission_level") or "medium"),
                "before_summary": _before_summary(computer),
                "after_summary": event.display_card.summary,
                "verification": verification,
                "duration_ms": _duration_ms(state),
                "status": "completed" if event.type == EventType.TOOL_COMPLETED else "failed",
            },
        )
        runtime = self._runtime.setdefault(session_id, SessionRuntime(session_id))
        status = str(verification.get("status") or "")
        if event.type == EventType.TOOL_FAILED or status == "likely_noop":
            runtime.repeated_noops += 1
        else:
            runtime.repeated_noops = 0
        signature = _observation_signature(computer, verification)
        if signature:
            runtime.seen_signatures.append(signature)
            runtime.seen_signatures = runtime.seen_signatures[-6:]
        pause_reason = ""
        if runtime.repeated_noops >= 3:
            pause_reason = "repeated_no_change"
        elif signature and runtime.seen_signatures.count(signature) >= 3:
            pause_reason = "action_loop_detected"
        elif _focus_drifted(computer):
            pause_reason = "focus_drift"
        if pause_reason:
            self.pause(session_id, pause_reason)
        budget = self.preflight(session_id)
        return {"ok": True, "receipt": receipt.get("receipt") or {}, "paused": bool(pause_reason) or not budget.get("allowed", True), "pause_reason": pause_reason or budget.get("reason", ""), "budget": budget}

    def pause(self, session_id: str, reason: str) -> dict[str, Any]:
        runtime = self._runtime.setdefault(session_id, SessionRuntime(session_id))
        runtime.pause_reason = reason
        # Persist the reason too: in-memory runtime dies with the process, and a
        # session that reappears after a restart still has to explain itself.
        result = self.store.transition_session(session_id, "paused", reason)
        result["pause_reason"] = reason
        return result

    def runtime_payload(self, session_id: str) -> dict[str, Any]:
        runtime = self._runtime.get(session_id) or SessionRuntime(session_id)
        preflight = self.preflight(session_id) if session_id else {"allowed": False, "reason": "session_not_found"}
        session = self.store.session_payload(session_id, include_receipts=False) if session_id else {}
        return {
            "model_calls": runtime.model_calls,
            # Fall back to the stored reason so a session recovered from a
            # previous launch still reports why it is paused.
            "pause_reason": runtime.pause_reason or str(session.get("pause_reason") or ""),
            "recovery_required": bool(session.get("recovery_required")),
            "repeated_noops": runtime.repeated_noops,
            "budget": preflight,
            "drivers": self.driver_inventory(str((self.store.session_payload(session_id, False) or {}).get("driver") or "auto")).payload() if session_id else self.driver_inventory().payload(),
        }


def _cua_available() -> bool:
    if not shutil.which("cua-driver"):
        return False
    try:
        from agent_companion.core.computer_use.cua_driver import CuaDriverBackend

        return CuaDriverBackend.available()
    except Exception:
        return False


def _failure_count(receipts: list[dict[str, Any]]) -> int:
    return sum(1 for row in receipts if row.get("status") == "failed" or str((row.get("verification") or {}).get("status") or "") == "likely_noop")


def _before_summary(computer: dict[str, Any]) -> str:
    title = str(computer.get("before_title") or "").strip()
    if title:
        return f"执行前窗口：{title[:180]}"
    return "执行前画面已记录" if computer.get("before_artifact") else ""


def _duration_ms(state: dict[str, Any]) -> float:
    for key in ("duration_ms", "elapsed_ms"):
        try:
            return max(0.0, float(state.get(key) or 0))
        except (TypeError, ValueError):
            continue
    try:
        return max(0.0, float(state.get("elapsed_seconds") or 0) * 1000)
    except (TypeError, ValueError):
        return 0.0


def _observation_signature(computer: dict[str, Any], verification: dict[str, Any]) -> str:
    observation = computer.get("observation") if isinstance(computer.get("observation"), dict) else {}
    signals = verification.get("signals") if isinstance(verification.get("signals"), dict) else {}
    parts = (
        str(observation.get("title") or ""),
        str(observation.get("width") or ""),
        str(observation.get("height") or ""),
        str(signals.get("image_changed")),
        str(signals.get("ocr_changed")),
    )
    return "|".join(parts) if any(parts[:3]) else ""


def _focus_drifted(computer: dict[str, Any]) -> bool:
    before = str(computer.get("before_title") or "").casefold()
    observation = computer.get("observation") if isinstance(computer.get("observation"), dict) else {}
    after = str(observation.get("title") or "").casefold()
    return bool(after and "joi" in after and "joi" not in before)
