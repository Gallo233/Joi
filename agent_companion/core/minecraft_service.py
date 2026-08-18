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
    QUERY_ACTIONS,
    SCREEN_ACTIONS,
    canonicalize_game_intent,
    canonicalize_minecraft_scope,
    check_intent_scope,
    estimated_world_changes,
)
from agent_companion.core.minecraft_memory import MinecraftWorldMemory
from agent_companion.core.minecraft_planner import compile_plan, compile_single_action
from agent_companion.core.minecraft_screen import MinecraftScreenCache, sanitized_screen_text
from agent_companion.core.minecraft_skills import MinecraftSkillLibrary


_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_PLAYER = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_CHAT_COMMAND_INTERVAL_SECONDS = 5.0
# How many goals stay replay-protected at once. Sized for hours of autonomy, not
# for the seconds in which a duplicate submit can actually arrive.
_MAX_TRACKED_GOALS = 512


class MinecraftGameService:
    """Core-owned authority for Minecraft permissions, budgets and receipts."""

    def __init__(
        self,
        collaboration: CollaborationStore,
        adapters: GameAdapterRegistry,
        screen_cache: MinecraftScreenCache | None = None,
        plan_compiler: Callable[[str], Any] | None = None,
        skills: MinecraftSkillLibrary | None = None,
    ) -> None:
        self.collaboration = collaboration
        self.adapters = adapters
        self.screen_cache = screen_cache
        self._plan_compiler = plan_compiler
        self.skills = skills
        self.memory = MinecraftWorldMemory(self.collaboration.data_home)
        self._lock = threading.RLock()
        self._runtime: dict[str, dict[str, Any]] = {}
        self._goals: dict[tuple[str, str], dict[str, Any]] = {}
        self._goal_order: list[tuple[str, str]] = []
        self._scope_approvals: dict[str, dict[str, Any]] = {}
        self._plan_approvals: dict[str, dict[str, Any]] = {}
        self._plans: dict[tuple[str, str], dict[str, Any]] = {}
        self._chat_last_accepted: dict[str, float | None] = {}
        self._plan_progress: Callable[[str, dict[str, Any]], None] | None = None

    def set_plan_progress_sink(self, sink: Callable[[str, dict[str, Any]], None] | None) -> None:
        """Where step-by-step plan progress is announced, if anywhere."""

        self._plan_progress = sink

    def _announce_plan(self, session_id: str, payload: dict[str, Any]) -> None:
        sink = self._plan_progress
        if sink is None:
            return
        try:
            sink(session_id, payload)
        except Exception:
            return

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
        autonomy: bool = False,
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
        if autonomy and str(intent.get("action") or "") == "attack":
            # Scheme A, second layer: even a compromised proposer can never make
            # autonomy attack. The voice-path gate is the first layer.
            return {"ok": False, "error": "autonomy_attack_forbidden", "zero_actions": True}
        intent_digest = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        key = (session_id, goal_id)
        preempt_goal = ""
        with self._lock:
            previous = self._goals.get(key)
            if previous:
                if previous["digest"] != intent_digest:
                    return {"ok": False, "error": "goal_id_conflict", "zero_actions": True}
                if previous.get("result") is not None:
                    return {**previous["result"], "replayed": True}
                return {"ok": False, "error": "goal_in_progress", "zero_actions": True}
            runtime = self._runtime.get(session_id)
            self._remember_goal(key, {"digest": intent_digest, "result": None})
            if runtime is not None and runtime.get("active_goal"):
                if autonomy:
                    # Autonomy never queues behind anything: skip this tick.
                    result = {"ok": False, "error": "minecraft_goal_already_running", "zero_actions": True}
                    self._goals[key]["result"] = result
                    return result
                if runtime.get("active_goal_autonomy"):
                    # B4: a user goal preempts the running autonomy goal. The
                    # cancelled goal's blocked submit returns goal.cancelled and
                    # cannot restart itself (no-replay).
                    preempt_goal = str(runtime["active_goal"])
                    runtime["active_goal"] = ""
                    runtime["active_goal_autonomy"] = False
                else:
                    result = {"ok": False, "error": "minecraft_goal_already_running", "zero_actions": True}
                    self._goals[key]["result"] = result
                    return result
            if runtime is not None:
                runtime["active_goal"] = goal_id
                runtime["active_goal_autonomy"] = bool(autonomy)
        if preempt_goal:
            self.adapters.cancel_minecraft_goal(session_id, preempt_goal)
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
        # Reading the world is low risk, but it still costs one of the actions the
        # user approved: max_actions is their ceiling on how much Joi does at all,
        # and observe/observe_screen have always been counted against it.
        risk = "low" if action in {"observe", "inventory", "observe_screen"} | QUERY_ACTIONS else "medium"
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
        if action == "load_skill":
            return self._submit_skill_goal(key, session_id, str(intent.get("name") or ""))
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
            # A query's whole point is the reading it came back with.
            "observation": str(bridge_result.get("detail") or "")[:600] if action in QUERY_ACTIONS else "",
            "summary": _public_action_summary(action, status, int(bridge_result.get("changes") or 0)),
            "receipt": receipt_result.get("receipt") if receipt_result.get("ok") else {},
            "session": _public_session(self.collaboration.session_payload(session_id)),
            "recovery_required": bool(bridge_result.get("recovery_required")),
        }
        return self._finish_goal(key, result)

    def _submit_skill_goal(self, key: tuple[str, str], session_id: str, name: str) -> dict[str, Any]:
        """Read one knowledge note. Core-side, read-only, same receipt chain."""

        text = self.skills.load(name) if self.skills is not None else ""
        ok = bool(text)
        receipt = self.collaboration.add_receipt(
            session_id,
            {
                "action": "minecraft.load_skill",
                "risk": "low",
                "before_summary": "skill_note",
                "after_summary": "skill_note",
                "verification": {"verified": ok, "blocks_changed": 0, "effects_observed": 0, "after_state_present": ok},
                "duration_ms": 0,
                "status": "completed" if ok else "failed",
            },
        )
        if not ok:
            self._release_budget_reservation(session_id, {"action": "load_skill"})
        return self._finish_goal(
            key,
            {
                "ok": ok,
                "error": "" if ok else "skill_note_not_found",
                "status": "completed" if ok else "failed",
                "summary": "skill_loaded" if ok else "skill_note_not_found",
                "observation": text,
                "receipt": receipt.get("receipt") if receipt.get("ok") else {},
                "session": _public_session(self.collaboration.session_payload(session_id)),
            },
        )

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
        if not ok:
            # A capture that produced nothing changed nothing either, so it
            # must not silently eat one of the user's approved actions.
            self._release_budget_reservation(session_id, {"action": action})
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

    def plan(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        """Compile one natural-language goal into a preview awaiting approval."""

        source = params if isinstance(params, Mapping) else {}
        session_id = _external_id(source.get("session_id"))
        if not session_id:
            return {"ok": False, "error": "session_id_required"}
        if self._plan_compiler is None:
            return {"ok": False, "error": "minecraft_planning_unavailable"}
        session = self.collaboration.session_payload(session_id, include_receipts=False)
        if not session or session.get("state") != "running":
            return {"ok": False, "error": "minecraft_session_not_runnable"}
        compiled = compile_plan(str(source.get("goal_text") or ""), self._plan_compiler)
        if not compiled.get("ok"):
            return {"ok": False, "error": str(compiled.get("error") or "plan_compile_failed")}
        plan_id = f"plan-{uuid.uuid4().hex}"
        approval_id = f"minecraft-plan-approval-{uuid.uuid4().hex}"
        digest = _plan_approval_digest(session_id, plan_id, compiled)
        with self._lock:
            self._prune_plan_approvals()
            self._plan_approvals[approval_id] = {
                "digest": digest,
                "expires_at": time.monotonic() + 300,
                "session_id": session_id,
                "plan_id": plan_id,
                "plan": compiled,
            }
        return {
            "ok": True,
            "requires_approval": True,
            "approval_id": approval_id,
            "plan_id": plan_id,
            "summary": compiled["summary"],
            "steps": compiled["steps"],
            "estimated_actions": compiled["estimated_actions"],
            "estimated_changes": compiled["estimated_changes"],
        }

    def execute_plan(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        """Run an approved plan step by step; every step keeps its own gates."""

        source = params if isinstance(params, Mapping) else {}
        session_id = _external_id(source.get("session_id"))
        plan_id = _external_id(source.get("plan_id"))
        approval_id = _external_id(source.get("approval_id"))
        if not session_id or not plan_id or not approval_id or source.get("confirmed") is not True:
            return {"ok": False, "error": "plan_approval_required", "requires_approval": True}
        with self._lock:
            self._prune_plan_approvals()
            approval = self._plan_approvals.pop(approval_id, None)
        if (
            not approval
            or approval.get("plan_id") != plan_id
            or approval.get("session_id") != session_id
            or approval.get("digest") != _plan_approval_digest(session_id, plan_id, approval.get("plan") or {})
        ):
            return {"ok": False, "error": "plan_approval_invalid", "requires_approval": True}
        plan = approval["plan"]
        with self._lock:
            if (session_id, plan_id) in self._plans:
                return {"ok": False, "error": "plan_already_exists"}
            self._plans[(session_id, plan_id)] = {
                "state": "running",
                "steps_total": len(plan["steps"]),
                "steps_done": 0,
                "summary": plan["summary"],
                "steps": list(plan["steps"]),
                "cancel_requested": False,
                "current_goal_id": "",
            }
        thread = threading.Thread(
            target=self._run_plan, args=(session_id, plan_id), name=f"minecraft-plan-{plan_id[-8:]}", daemon=True
        )
        thread.start()
        return {"ok": True, "state": "running", "plan_id": plan_id}

    def plan_status(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        source = params if isinstance(params, Mapping) else {}
        session_id = _external_id(source.get("session_id"))
        plan_id = _external_id(source.get("plan_id"))
        with self._lock:
            row = self._plans.get((session_id, plan_id))
        if row is None:
            return {"ok": False, "error": "plan_not_found"}
        return {
            "ok": True,
            "state": row["state"],
            "steps_total": row["steps_total"],
            "steps_done": row["steps_done"],
            "summary": row["summary"],
        }

    def plan_cancel(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        source = params if isinstance(params, Mapping) else {}
        session_id = _external_id(source.get("session_id"))
        plan_id = _external_id(source.get("plan_id"))
        with self._lock:
            row = self._plans.get((session_id, plan_id))
            if row is not None:
                row["cancel_requested"] = True
            current_goal = str(row.get("current_goal_id") or "") if row is not None else ""
        if row is None:
            return {"ok": False, "error": "plan_not_found"}
        if current_goal:
            self.adapters.cancel_minecraft_goal(session_id, current_goal)
        return {"ok": True, "state": "cancelling", "plan_id": plan_id}

    def handle_chat(self, session_id: str, player: str, text: str) -> dict[str, Any]:
        """A whitelisted player's in-game chat line becomes at most one action.

        Runs off the bridge reader thread: the compile + submit happen on a
        worker so the event pump never blocks. Busy sessions skip (B4).
        """

        session_id = _external_id(session_id)
        player = str(player or "").strip()
        if not session_id or not _PLAYER.fullmatch(player) or not text.strip():
            return {"ok": False, "error": "chat_command_invalid"}
        session = self.collaboration.session_payload(session_id, include_receipts=False)
        if not session or session.get("state") != "running":
            return {"ok": False, "error": "minecraft_session_not_runnable"}
        permission = self.collaboration.permission_for_session(session_id)
        scope_wrapper = permission.get("scope") if isinstance(permission.get("scope"), dict) else {}
        scope = scope_wrapper.get("minecraft") if isinstance(scope_wrapper.get("minecraft"), dict) else {}
        allowed_players = {str(name).casefold() for name in (scope.get("allowed_players") or [])}
        if player.casefold() not in allowed_players:
            return {"ok": False, "error": "chat_player_out_of_scope"}
        if self._plan_compiler is None:
            return {"ok": False, "error": "minecraft_planning_unavailable"}
        # Chat arrives as fast as anyone can type, and every line would
        # otherwise cost a model call. One command per interval per session is
        # plenty for a companion and makes chat flooding cheap to absorb.
        now = time.monotonic()
        with self._lock:
            # No default timestamp: `monotonic()` starts near zero in some
            # runtimes, so a zero sentinel would throttle the very first line.
            last = self._chat_last_accepted.get(session_id)
            if last is not None and now - last < _CHAT_COMMAND_INTERVAL_SECONDS:
                return {"ok": False, "error": "chat_command_throttled"}
            self._chat_last_accepted[session_id] = now
        bounded = " ".join(str(text).split())[:300]

        def worker() -> None:
            try:
                intent = compile_single_action(bounded, self._plan_compiler)
                if intent is None:
                    return
                self.submit_goal(
                    {
                        "session_id": session_id,
                        "goal_id": f"chat-goal-{uuid.uuid4().hex}",
                        "final": True,
                        "source": "text",
                        "intent": intent,
                    }
                )
            except Exception:
                # This runs detached from any caller: a shutting-down store or a
                # provider error must not take the process with it.
                return

        threading.Thread(target=worker, name=f"minecraft-chat-{session_id[-8:]}", daemon=True).start()
        return {"ok": True, "queued": True}

    def _run_plan(self, session_id: str, plan_id: str) -> None:
        with self._lock:
            row = self._plans.get((session_id, plan_id))
        if row is None:
            return
        steps = list(row.get("steps") or [])
        summary = str(row.get("summary") or "")
        self._announce_plan(session_id, {"state": "running", "summary": summary, "steps_total": len(steps), "steps_done": 0})
        for index, step in enumerate(steps):
            with self._lock:
                cancelled = bool(row.get("cancel_requested"))
                if cancelled:
                    row["state"] = "cancelled"
                else:
                    goal_id = f"plan-goal-{uuid.uuid4().hex}"
                    row["current_goal_id"] = goal_id
            if cancelled:
                self._announce_plan(
                    session_id,
                    {"state": "cancelled", "summary": summary, "steps_total": len(steps), "steps_done": index},
                )
                return
            self._announce_plan(
                session_id,
                {
                    "state": "step",
                    "summary": summary,
                    "steps_total": len(steps),
                    "steps_done": index,
                    "action": str(step.get("action") or ""),
                },
            )
            try:
                result = self.submit_goal(
                    {"session_id": session_id, "goal_id": goal_id, "final": True, "source": "voice", "intent": step}
                )
            except Exception:
                # The plan outlives no one: ending the session (or Core) closes
                # the store under this thread, and that is a stop, not a crash.
                with self._lock:
                    row["state"] = "failed"
                    row["current_goal_id"] = ""
                return
            with self._lock:
                row["steps_done"] += 1
                row["current_goal_id"] = ""
            if not result.get("ok"):
                with self._lock:
                    row["state"] = "failed"
                self._announce_plan(
                    session_id,
                    {"state": "failed", "summary": summary, "steps_total": len(steps), "steps_done": index, "action": str(step.get("action") or "")},
                )
                return
        with self._lock:
            row["state"] = "completed"
        self._announce_plan(session_id, {"state": "completed", "summary": summary, "steps_total": len(steps), "steps_done": len(steps)})

    def pending_plan_approval_ids(self) -> list[str]:
        """Which plan cards are still answerable, for a reloaded conversation."""

        with self._lock:
            self._prune_plan_approvals()
            return sorted(self._plan_approvals)

    def has_pending_plan_approval(self, approval_id: str) -> bool:
        """Whether this id is a plan of ours still waiting to be answered.

        Lets one approval card in the conversation resolve here instead of in
        the tool-step registry, without either side guessing at the other's ids.
        """

        with self._lock:
            self._prune_plan_approvals()
            return _external_id(approval_id) in self._plan_approvals

    def resolve_plan_approval(self, approval_id: str, approved: bool) -> dict[str, Any]:
        """Run or drop a compiled plan the user just answered.

        Declining forgets the plan outright: a preview the user said no to must
        not stay executable, and re-asking costs one compile.
        """

        approval_id = _external_id(approval_id)
        with self._lock:
            self._prune_plan_approvals()
            approval = self._plan_approvals.get(approval_id)
            if approval is None:
                return {"ok": False, "error": "plan_approval_invalid"}
            if not approved:
                self._plan_approvals.pop(approval_id, None)
                return {"ok": True, "state": "cancelled", "plan_id": str(approval.get("plan_id") or "")}
            session_id = str(approval.get("session_id") or "")
            plan_id = str(approval.get("plan_id") or "")
        # execute_plan re-checks the digest and consumes the approval itself, so
        # the one-time binding still holds even though this read did not pop it.
        return self.execute_plan(
            {"session_id": session_id, "plan_id": plan_id, "approval_id": approval_id, "confirmed": True}
        )

    def _prune_plan_approvals(self) -> None:
        now = time.monotonic()
        self._plan_approvals = {
            key: value for key, value in self._plan_approvals.items() if float(value.get("expires_at") or 0) > now
        }

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
        # Before the bridge closes: a running plan loops over its own steps and
        # would keep submitting them into a session that no longer exists. Each
        # submit failed closed, so nothing reached the world, but the plan only
        # noticed by failing a step instead of by being told to stop.
        self._cancel_session_plans(session_id)
        memory = self._session_memory_input(session_id)
        result = self.adapters.stop_minecraft_session(session_id)
        if result.get("ok") or result.get("forced_terminated"):
            self.collaboration.transition_session(session_id, "cancelled")
            self._forget_session(session_id)
            if memory is not None:
                self.memory.remember(
                    memory["server_id"],
                    memory["world"],
                    observation=memory["observation"],
                    recent_goals=memory["recent_goals"],
                    workstations=memory.get("workstations"),
                )
        return {**result, "session": _public_session(self.collaboration.session_payload(session_id))}

    def world_memory(self, session_id: str) -> str:
        """Sanitized per-world memory for prompts; coordinates never cross."""

        memory = self._session_memory_input(session_id)
        if memory is None:
            return ""
        return self.memory.summary(memory["server_id"], memory["world"])

    def _session_memory_input(self, session_id: str) -> dict[str, Any] | None:
        permission = self.collaboration.permission_for_session(session_id)
        scope_wrapper = permission.get("scope") if isinstance(permission.get("scope"), dict) else {}
        scope = scope_wrapper.get("minecraft") if isinstance(scope_wrapper.get("minecraft"), dict) else {}
        server_id = str(scope.get("server_id") or "")
        world = str(scope.get("world") or "")
        if not server_id or not world:
            return None
        snapshot = self.adapters.minecraft_snapshot(session_id)
        observation = snapshot.get("observation") if isinstance(snapshot.get("observation"), dict) else {}
        receipts = self.collaboration.list_receipts(session_id, limit=12)
        recent_goals = [str(row.get("action") or "") for row in receipts if isinstance(row, dict)]
        # What this world actually has, read off what Joi can see right now.
        blocks = observation.get("nearby_blocks") if isinstance(observation.get("nearby_blocks"), list) else []
        workstations = [str(row.get("name") or "") for row in blocks if isinstance(row, Mapping)]
        return {
            "server_id": server_id,
            "world": world,
            "observation": observation,
            "recent_goals": recent_goals,
            "workstations": workstations,
        }

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

    def active_session_ids(self) -> list[str]:
        """The Minecraft sessions this service currently holds runtime for."""

        with self._lock:
            return sorted(self._runtime)

    def _cancel_session_plans(self, session_id: str) -> None:
        """Ask every plan of this session to stop at its next step boundary."""

        with self._lock:
            rows = [(key, row) for key, row in self._plans.items() if key[0] == session_id]
            for _key, row in rows:
                row["cancel_requested"] = True
            goals = [str(row.get("current_goal_id") or "") for _key, row in rows]
        for goal_id in goals:
            if goal_id:
                self.adapters.cancel_minecraft_goal(session_id, goal_id)

    def _forget_session(self, session_id: str) -> None:
        """Drop everything keyed to a session that is over.

        Only ``_runtime`` used to be released here, so each finished session left
        its goals -- each holding a full result payload with a session snapshot
        and a receipt -- its plans and its chat throttle behind for as long as
        Core ran. Receipts and world memory are the durable record; these are
        in-flight bookkeeping and have nothing to say once the world is gone.
        """

        with self._lock:
            self._runtime.pop(session_id, None)
            self._chat_last_accepted.pop(session_id, None)
            for key in [key for key in self._goals if key[0] == session_id]:
                self._goals.pop(key, None)
                self._goal_order = [row for row in self._goal_order if row != key]
            for key in [key for key in self._plans if key[0] == session_id]:
                self._plans.pop(key, None)

    def _remember_goal(self, key: tuple[str, str], row: dict[str, Any]) -> None:
        """Register a goal for no-replay, keeping the registry bounded.

        A session with autonomy running submits a goal every interval for hours,
        so this cannot grow with the session. The cap is far above any window in
        which a duplicate submit can arrive -- duplicates come from the dispatch
        handshake and land within seconds -- so eviction never re-opens a goal
        that could still be replayed.
        """

        self._goals[key] = row
        self._goal_order.append(key)
        while len(self._goal_order) > _MAX_TRACKED_GOALS:
            stale = self._goal_order.pop(0)
            if stale != key:
                self._goals.pop(stale, None)

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
                runtime["active_goal_autonomy"] = False
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


def _plan_approval_digest(session_id: str, plan_id: str, plan: Mapping[str, Any]) -> str:
    payload = {
        "session_id": session_id,
        "plan_id": plan_id,
        "summary": plan.get("summary"),
        "steps": list(plan.get("steps") or []),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _receipt_status(result: Mapping[str, Any]) -> str:
    if result.get("verified") is True and isinstance(result.get("after"), dict) and result.get("after"):
        return "completed"
    if str(result.get("status") or "") == "partial":
        return "partial"
    if result.get("recovery_required") or int(result.get("changes") or 0) > 0 or int(result.get("effects") or 0) > 0:
        return "unverified"
    if str(result.get("status") or "") == "cancelled":
        # A goal that was stopped before it did anything is not a failure. It
        # used to be counted as one, so three user interruptions of Joi's own
        # autonomy -- or three plan cancellations -- exhausted the failure
        # budget and paused the whole session.
        return "cancelled"
    return "failed"


def _result_matches_intent(intent: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    action = str(intent.get("action") or "")
    changes = max(0, int(result.get("changes") or 0))
    effects = max(0, int(result.get("effects") or 0))
    if action in {"observe", "inventory", "observe_screen"}:
        return changes == 0 and effects == 0
    if action in {"follow_player", "come_to_player", "eat"}:
        return effects >= 1
    if action == "attack":
        return effects >= int(intent.get("count") or 1)
    if action in {"flee", "guard"}:
        # Verified by the bridge's own distance/state checks; no hostile nearby
        # is a legitimate zero-effect completion.
        return True
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
    if status == "cancelled":
        return f"{action} cancelled before it changed anything"
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
