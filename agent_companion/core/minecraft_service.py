from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Any, Callable, Mapping
import uuid

from agent_companion.core.collaboration_store import CollaborationStore, RECOVERY_REQUIRED
from agent_companion.core.game_adapters import GameAdapterRegistry
from agent_companion.core.minecraft_contract import (
    MinecraftContractError,
    SCREEN_ACTIONS,
    canonicalize_game_intent,
    canonicalize_minecraft_scope,
    check_intent_scope,
    estimated_world_changes,
)
from agent_companion.core.minecraft_screen import MinecraftScreenCache, sanitized_screen_text


_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")


class MinecraftGameService:
    """Core-owned authority for Minecraft permissions, budgets and receipts."""

    def __init__(
        self,
        collaboration: CollaborationStore,
        adapters: GameAdapterRegistry,
        screen_cache: MinecraftScreenCache | None = None,
    ) -> None:
        self.collaboration = collaboration
        self.adapters = adapters
        self.screen_cache = screen_cache
        self._lock = threading.RLock()
        self._runtime: dict[str, dict[str, Any]] = {}
        self._goals: dict[tuple[str, str], dict[str, Any]] = {}
        self._scope_approvals: dict[str, dict[str, Any]] = {}

    def start_session(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        source = params if isinstance(params, Mapping) else {}
        mode = str(source.get("mode") or "companion")
        if mode not in {"companion", "delegate"}:
            return {"ok": False, "error": "unsupported_game_mode", "supported_modes": ["companion", "delegate"]}
        try:
            scope = canonicalize_minecraft_scope(source.get("scope") if isinstance(source.get("scope"), Mapping) else None)
        except MinecraftContractError as exc:
            return {"ok": False, "error": exc.code}
        approval_digest = _scope_approval_digest(mode, scope, source.get("budget"))
        if source.get("confirmed_scope") is not True:
            approval_id = f"minecraft-approval-{uuid.uuid4().hex}"
            with self._lock:
                self._prune_scope_approvals()
                self._scope_approvals[approval_id] = {"digest": approval_digest, "expires_at": time.monotonic() + 300}
            return {
                "ok": False,
                "error": "minecraft_scope_confirmation_required",
                "requires_approval": True,
                "approval_id": approval_id,
                "scope_preview": _public_scope(scope),
            }
        approval_id = _external_id(source.get("approval_id"))
        with self._lock:
            self._prune_scope_approvals()
            approval = self._scope_approvals.pop(approval_id, None) if approval_id else None
        if not approval or approval.get("digest") != approval_digest:
            return {"ok": False, "error": "minecraft_scope_approval_invalid", "requires_approval": True}
        budget = source.get("budget") if isinstance(source.get("budget"), dict) else {}
        profile = "delegate" if mode == "delegate" else "collaborate"
        started = self.collaboration.start_session(
            "game",
            str(source.get("goal_summary") or "Minecraft session")[:300],
            profile,
            driver="minecraft_game_adapter_v2",
            budget=budget,
            stop_conditions=["user_input", "game_disconnect", "budget_exhausted", "scope_violation"],
        )
        if not started.get("ok"):
            return started
        session_id = str((started.get("session") or {}).get("id") or "")
        grant = self.collaboration.grant_permission(
            session_id,
            profile,
            {"game": ["Minecraft"], "minecraft": scope},
        )
        if not grant.get("ok"):
            self.collaboration.transition_session(session_id, "failed")
            return {"ok": False, "error": "minecraft_permission_grant_failed"}
        bridge = self.adapters.start_minecraft_session(session_id, mode, scope, budget)
        if not bridge.get("ok"):
            self.collaboration.revoke_permission(session_id)
            self.collaboration.transition_session(session_id, "failed")
            return {"ok": False, "error": str(bridge.get("error") or "minecraft_bridge_failed"), "session": _public_session(self.collaboration.session_payload(session_id))}
        session = self.collaboration.session_payload(session_id)
        with self._lock:
            self._runtime[session_id] = {
                "scope": scope,
                "actions_reserved": 0,
                "blocks_reserved": 0,
                "failures": 0,
                "started_at": time.monotonic(),
                "max_steps": min(int(scope["max_actions"]), int((session.get("budget") or {}).get("max_steps") or scope["max_actions"])),
                "max_seconds": int((session.get("budget") or {}).get("max_seconds") or 900),
                "max_failures": int((session.get("budget") or {}).get("max_failures") or 3),
            }
        return {
            "ok": True,
            "state": "ready",
            "session": _public_session(self.collaboration.session_payload(session_id)),
            "capabilities": bridge.get("capabilities") if isinstance(bridge.get("capabilities"), list) else [],
        }

    def submit_goal(
        self,
        params: Mapping[str, Any] | None,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_registered: Callable[[], None] | None = None,
        on_submitted: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        source = params if isinstance(params, Mapping) else {}
        if _cancelled(cancel_requested):
            return {"ok": False, "error": "goal_cancelled", "status": "cancelled", "zero_actions": True}
        session_id = _external_id(source.get("session_id"))
        goal_id = _external_id(source.get("goal_id"))
        if not session_id or not goal_id:
            return {"ok": False, "error": "session_and_goal_required"}
        try:
            intent = canonicalize_game_intent(
                {
                    "final": source.get("final"),
                    "source": source.get("source"),
                    "intent": source.get("intent"),
                }
            )
        except MinecraftContractError as exc:
            return {"ok": False, "error": exc.code, "zero_actions": True}
        intent_digest = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        key = (session_id, goal_id)
        with self._lock:
            previous = self._goals.get(key)
            if previous:
                if previous["digest"] != intent_digest:
                    return {"ok": False, "error": "goal_id_conflict", "zero_actions": True}
                if previous.get("result") is not None:
                    return {**previous["result"], "replayed": True}
                return {"ok": False, "error": "goal_in_progress", "zero_actions": True}
            runtime = self._runtime.get(session_id)
            self._goals[key] = {"digest": intent_digest, "result": None}
            if runtime is not None and runtime.get("active_goal"):
                result = {"ok": False, "error": "minecraft_goal_already_running", "zero_actions": True}
                self._goals[key]["result"] = result
                return result
            if runtime is not None:
                runtime["active_goal"] = goal_id
        if runtime is None:
            return self._finish_goal(key, {"ok": False, "error": "minecraft_session_not_found", "zero_actions": True})
        if _cancelled(cancel_requested):
            return self._finish_goal(key, {"ok": False, "error": "goal_cancelled", "status": "cancelled", "zero_actions": True})
        session = self.collaboration.session_payload(session_id, include_receipts=False)
        if not session or session.get("state") != "running":
            return self._finish_goal(key, {"ok": False, "error": "minecraft_session_not_runnable", "zero_actions": True})
        permission = self.collaboration.permission_for_session(session_id)
        live_scope_wrapper = permission.get("scope") if isinstance(permission.get("scope"), dict) else {}
        live_scope = live_scope_wrapper.get("minecraft") if isinstance(live_scope_wrapper.get("minecraft"), dict) else None
        if live_scope is None:
            return self._finish_goal(key, {"ok": False, "error": "minecraft_scope_grant_missing", "zero_actions": True})
        try:
            live_scope = canonicalize_minecraft_scope(live_scope)
        except MinecraftContractError:
            return self._finish_goal(key, {"ok": False, "error": "minecraft_scope_grant_invalid", "zero_actions": True})
        denial = check_intent_scope(intent, live_scope)
        if denial:
            return self._finish_goal(key, {"ok": False, "error": denial, "zero_actions": True})
        action = str(intent["action"])
        risk = "low" if action in {"observe", "inventory", "observe_screen"} else "medium"
        gate = self.collaboration.action_allowed(session_id, f"game.minecraft.{action}", risk, effect_kind="")
        if not gate.get("allowed"):
            return self._finish_goal(
                key,
                {
                    "ok": False,
                    "error": "minecraft_action_not_authorized",
                    "requires_approval": bool(gate.get("requires_approval")),
                    "zero_actions": True,
                },
            )
        reservation = self._reserve_budget(session_id, intent, live_scope)
        if reservation:
            self.collaboration.transition_session(session_id, "paused", reservation)
            return self._finish_goal(key, {"ok": False, "error": reservation, "zero_actions": True, "session": _public_session(self.collaboration.session_payload(session_id))})
        if _cancelled(cancel_requested):
            return self._finish_goal(key, {"ok": False, "error": "goal_cancelled", "status": "cancelled", "zero_actions": True})
        if action in SCREEN_ACTIONS:
            return self._submit_screen_goal(key, session_id, goal_id, action)
        started_at = time.monotonic()
        if cancel_requested is None and on_registered is None and on_submitted is None:
            bridge_result = self.adapters.submit_minecraft_goal(session_id, goal_id, intent)
        else:
            bridge_result = self.adapters.submit_minecraft_goal(
                session_id,
                goal_id,
                intent,
                cancel_requested=cancel_requested,
                on_registered=on_registered,
                on_submitted=on_submitted,
            )
        duration_ms = (time.monotonic() - started_at) * 1000
        if bridge_result.get("zero_actions"):
            self._release_budget_reservation(session_id, intent)
            return self._finish_goal(
                key,
                {
                    "ok": False,
                    "error": str(bridge_result.get("error") or "goal_cancelled"),
                    "status": "cancelled",
                    "verified": False,
                    "changes": 0,
                    "effects": 0,
                    "zero_actions": True,
                    "session": _public_session(self.collaboration.session_payload(session_id)),
                },
            )
        if bridge_result.get("verified") is True and not _result_matches_intent(intent, bridge_result):
            bridge_result["verified"] = False
            bridge_result["error"] = "verification_failed"
        status = _receipt_status(bridge_result)
        if status == "failed":
            with self._lock:
                runtime["failures"] += 1
        receipt_result = self.collaboration.add_receipt(
            session_id,
            {
                "action": f"minecraft.{action}",
                "risk": risk,
                "before_summary": _observation_summary(bridge_result.get("before")),
                "after_summary": _observation_summary(bridge_result.get("after")),
                "verification": {
                    "verified": bridge_result.get("verified") is True,
                    "blocks_changed": max(0, int(bridge_result.get("changes") or 0)),
                    "effects_observed": max(0, int(bridge_result.get("effects") or 0)),
                    "after_state_present": isinstance(bridge_result.get("after"), dict) and bool(bridge_result.get("after")),
                    "private_checkpoint_persisted": bridge_result.get("checkpoint_persisted") is True,
                },
                "duration_ms": duration_ms,
                "status": status,
            },
        )
        if bridge_result.get("recovery_required"):
            self.collaboration.transition_session(session_id, "paused", RECOVERY_REQUIRED)
        elif status == "failed" and runtime["failures"] >= runtime["max_failures"]:
            self.collaboration.transition_session(session_id, "paused", "failure_budget_exhausted")
        result = {
            "ok": status == "completed",
            "error": "" if status == "completed" else str(bridge_result.get("error") or status),
            "status": status,
            "summary": _public_action_summary(action, status, int(bridge_result.get("changes") or 0)),
            "receipt": receipt_result.get("receipt") if receipt_result.get("ok") else {},
            "session": _public_session(self.collaboration.session_payload(session_id)),
            "recovery_required": bool(bridge_result.get("recovery_required")),
        }
        return self._finish_goal(key, result)

    def _submit_screen_goal(self, key: tuple[str, str], session_id: str, goal_id: str, action: str) -> dict[str, Any]:
        """Core-side read-only screen observation, same gate and receipt chain.

        The bridge never sees this action. The screen text is the sanitized
        cache projection only; the observation object (paths, geometry,
        handles) stays inside Core.
        """

        started_at = time.monotonic()
        if self.screen_cache is None:
            screen = {"ok": False, "text": "", "error": "screen_observation_unavailable"}
        else:
            screen = self.screen_cache.refresh()
        ok = bool(screen.get("ok")) and bool(screen.get("text"))
        status = "completed" if ok else "failed"
        receipt_result = self.collaboration.add_receipt(
            session_id,
            {
                "action": f"minecraft.{action}",
                "risk": "low",
                "before_summary": "screen_observation",
                "after_summary": "screen_observation",
                "verification": {
                    "verified": ok,
                    "blocks_changed": 0,
                    "effects_observed": 0,
                    "after_state_present": ok,
                },
                "duration_ms": int((time.monotonic() - started_at) * 1000),
                "status": status,
            },
        )
        return self._finish_goal(
            key,
            {
                "ok": ok,
                "error": "" if ok else str(screen.get("error") or "screen_observation_failed"),
                "status": status,
                "summary": "screen_observed" if ok else "screen_observation_failed",
                "observation": sanitized_screen_text(screen) if ok else "",
                "receipt": receipt_result.get("receipt") if receipt_result.get("ok") else {},
                "session": _public_session(self.collaboration.session_payload(session_id)),
            },
        )

    def pause(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        return self._control(params, "pause")

    def resume(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        return self._control(params, "resume")

    def cancel(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        return self._control(params, "cancel")

    def stop_session(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        source = params if isinstance(params, Mapping) else {}
        session_id = _external_id(source.get("session_id"))
        if not session_id:
            return {"ok": False, "error": "session_id_required"}
        result = self.adapters.stop_minecraft_session(session_id)
        if result.get("ok") or result.get("forced_terminated"):
            self.collaboration.transition_session(session_id, "cancelled")
            with self._lock:
                self._runtime.pop(session_id, None)
        return {**result, "session": _public_session(self.collaboration.session_payload(session_id))}

    def status(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        session_id = _external_id((params or {}).get("session_id") if isinstance(params, Mapping) else "")
        if not session_id:
            return {"ok": False, "error": "session_id_required"}
        bridge = self.adapters.minecraft_session_status(session_id)
        return {"ok": bool(bridge.get("ok")), "bridge_state": bridge.get("state"), "active_goal": bridge.get("active_goal"), "session": _public_session(self.collaboration.session_payload(session_id))}

    def shutdown(self) -> None:
        self.adapters.shutdown()

    def _control(self, params: Mapping[str, Any] | None, action: str) -> dict[str, Any]:
        source = params if isinstance(params, Mapping) else {}
        session_id = _external_id(source.get("session_id"))
        goal_id = _external_id(source.get("goal_id"))
        if not session_id or not goal_id:
            return {"ok": False, "error": "session_and_goal_required"}
        operation = {
            "pause": self.adapters.pause_minecraft_goal,
            "resume": self.adapters.resume_minecraft_goal,
            "cancel": self.adapters.cancel_minecraft_goal,
        }[action]
        result = operation(session_id, goal_id)
        if result.get("ok"):
            self.collaboration.transition_session(session_id, {"pause": "paused", "resume": "running", "cancel": "running"}[action])
        elif result.get("forced_terminated"):
            if action == "cancel":
                self.collaboration.transition_session(session_id, "cancelled")
            else:
                self.collaboration.transition_session(session_id, "paused", RECOVERY_REQUIRED)
        return {**result, "session": _public_session(self.collaboration.session_payload(session_id))}

    def _reserve_budget(self, session_id: str, intent: Mapping[str, Any], live_scope: Mapping[str, Any]) -> str:
        with self._lock:
            runtime = self._runtime.get(session_id)
            if runtime is None:
                return "minecraft_session_not_found"
            if time.monotonic() - runtime["started_at"] >= runtime["max_seconds"]:
                return "time_budget_exhausted"
            effective_max_steps = min(int(runtime["max_steps"]), int(live_scope["max_actions"]))
            if runtime["actions_reserved"] + 1 > effective_max_steps:
                return "action_budget_exhausted"
            changes = estimated_world_changes(intent)
            if runtime["blocks_reserved"] + changes > int(live_scope["max_blocks_changed"]):
                return "block_budget_exhausted"
            runtime["actions_reserved"] += 1
            runtime["blocks_reserved"] += changes
        return ""

    def _release_budget_reservation(self, session_id: str, intent: Mapping[str, Any]) -> None:
        """Roll back a reservation only when the bridge proves no submit occurred."""
        with self._lock:
            runtime = self._runtime.get(session_id)
            if runtime is None:
                return
            runtime["actions_reserved"] = max(0, int(runtime["actions_reserved"]) - 1)
            runtime["blocks_reserved"] = max(0, int(runtime["blocks_reserved"]) - estimated_world_changes(intent))

    def _prune_scope_approvals(self) -> None:
        now = time.monotonic()
        self._scope_approvals = {key: value for key, value in self._scope_approvals.items() if float(value.get("expires_at") or 0) > now}

    def _finish_goal(self, key: tuple[str, str], result: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            row = self._goals.get(key)
            if row is not None:
                row["result"] = result
            runtime = self._runtime.get(key[0])
            if runtime is not None and runtime.get("active_goal") == key[1]:
                runtime["active_goal"] = ""
        return result


def _external_id(value: Any) -> str:
    clean = str(value or "").strip()
    return clean if _EXTERNAL_ID.fullmatch(clean) else ""


def _cancelled(check: Callable[[], bool] | None) -> bool:
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:
        return True


def _scope_approval_digest(mode: str, scope: Mapping[str, Any], budget: Any) -> str:
    payload = {"mode": mode, "scope": dict(scope), "budget": budget if isinstance(budget, dict) else {}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _receipt_status(result: Mapping[str, Any]) -> str:
    if result.get("verified") is True and isinstance(result.get("after"), dict) and result.get("after"):
        return "completed"
    if str(result.get("status") or "") == "partial":
        return "partial"
    if result.get("recovery_required") or int(result.get("changes") or 0) > 0 or int(result.get("effects") or 0) > 0:
        return "unverified"
    return "failed"


def _result_matches_intent(intent: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    action = str(intent.get("action") or "")
    changes = max(0, int(result.get("changes") or 0))
    effects = max(0, int(result.get("effects") or 0))
    if action in {"observe", "inventory"}:
        return changes == 0 and effects == 0
    if action in {"follow_player", "come_to_player", "eat"}:
        return effects >= 1
    if action in {"collect", "mine"}:
        requested = int(intent.get("count") or 0)
        return changes == requested and effects >= requested
    if action == "craft":
        return effects >= int(intent.get("count") or 0)
    if action == "place_blueprint":
        return changes <= len(intent.get("blocks") or []) and effects == changes
    if action == "deposit":
        return effects >= sum(int(row.get("count") or 0) for row in intent.get("items") or [])
    return False


def _observation_summary(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "observation_unavailable"
    return "dimension={}; health={}; food={}; inventory_slots={}".format(
        str(value.get("dimension") or "unknown")[:24],
        max(0, int(value.get("health") or 0)),
        max(0, int(value.get("food") or 0)),
        max(0, int(value.get("inventory_slots") or 0)),
    )


def _public_action_summary(action: str, status: str, changes: int) -> str:
    if status == "completed":
        return f"{action} verified; {max(0, changes)} world changes"
    if status == "partial":
        return f"{action} cancelled after a partial result"
    if status == "unverified":
        return f"{action} stopped; completion could not be verified"
    return f"{action} failed without a verified completion"


def _public_scope(scope: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "server_id": scope.get("server_id"),
        "world": scope.get("world"),
        "dimensions": list(scope.get("dimensions") or []),
        "max_radius": scope.get("max_radius"),
        "max_actions": scope.get("max_actions"),
        "max_blocks_changed": scope.get("max_blocks_changed"),
        "allowed_blocks": list(scope.get("allowed_blocks") or []),
        "allowed_players": list(scope.get("allowed_players") or []),
        "allow_build": bool(scope.get("allow_build")),
        "allow_containers": bool(scope.get("allow_containers")),
    }


def _public_session(session: Any) -> dict[str, Any]:
    if not isinstance(session, Mapping):
        return {}
    return {
        "id": str(session.get("id") or ""),
        "capability": str(session.get("capability") or ""),
        "permission_profile": str(session.get("permission_profile") or ""),
        "state": str(session.get("state") or ""),
        "pause_reason": str(session.get("pause_reason") or ""),
        "recovery_required": bool(session.get("recovery_required")),
        "budget": dict(session.get("budget") or {}),
    }
