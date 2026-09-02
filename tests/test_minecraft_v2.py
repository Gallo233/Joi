from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_companion.core.minecraft_contract import (
    MinecraftContractError,
    canonicalize_game_intent,
    canonicalize_minecraft_scope,
    check_intent_scope,
    estimated_world_changes,
)
from agent_companion.core.minecraft_bridge import MinecraftBridgeClient
from agent_companion.core.collaboration_store import CollaborationStore
from agent_companion.core.minecraft_service import MinecraftGameService
from agent_companion.core.server import JsonRpcBridge
from agent_companion.core.game_adapters import GameAdapterRegistry


SAFE_SCOPE = {
    "server_id": "local-survival",
    "world": "world",
    "dimensions": ["overworld"],
    "max_radius": 32,
    "max_actions": 20,
    "max_blocks_changed": 64,
    "allowed_blocks": ["oak_log", "oak_planks", "cobblestone", "crafting_table", "chest"],
    "allowed_players": ["Player"],
    "allow_build": True,
    "allow_containers": True,
}


class MinecraftContractTests(unittest.TestCase):
    def test_all_p2_primitives_have_strict_canonical_schemas(self) -> None:
        samples = (
            {"action": "observe", "dimension": "overworld", "radius": 12},
            {"action": "inventory"},
            {"action": "follow_player", "player": "Player", "distance": 3, "duration_seconds": 10},
            {"action": "come_to_player", "player": "Player", "distance": 2},
            {"action": "collect", "block": "oak_log", "count": 2, "radius": 16, "dimension": "overworld"},
            {"action": "mine", "block": "cobblestone", "count": 3, "radius": 16, "dimension": "overworld"},
            {"action": "craft", "item": "oak_planks", "count": 4},
            {"action": "eat", "item": "bread"},
            {
                "action": "place_blueprint",
                "anchor": "bot",
                "dimension": "overworld",
                "blocks": [
                    {"offset": [1, 0, 0], "block": "cobblestone"},
                    {"offset": [1, 1, 0], "block": "oak_planks"},
                ],
            },
            {"action": "deposit", "container": "chest", "items": [{"item": "oak_log", "count": 2}], "radius": 8},
            {"action": "attack", "count": 2, "radius": 12, "dimension": "overworld"},
            {"action": "flee", "distance": 12, "duration_seconds": 15, "dimension": "overworld"},
            {"action": "guard", "dimension": "overworld"},
        )
        for sample in samples:
            with self.subTest(action=sample["action"]):
                intent = canonicalize_game_intent({"final": True, "source": "voice", "intent": sample})
                self.assertEqual(intent["action"], sample["action"])

    def test_partial_transcript_unknown_actions_and_extra_fields_fail_closed(self) -> None:
        cases = (
            ({"final": False, "source": "voice", "intent": {"action": "observe"}}, "final_transcript_required"),
            ({"final": True, "source": "voice", "intent": {"action": "explore"}}, "unknown_game_action"),
            ({"final": True, "source": "voice", "intent": {"action": "observe", "goal": "随便逛逛"}}, "unexpected_intent_field"),
            ({"final": True, "source": "voice", "intent": {"action": "observe", "javascript": "process.exit()"}}, "forbidden_code_field"),
            ({"final": True, "source": "unknown", "intent": {"action": "observe"}}, "invalid_intent_source"),
        )
        for payload, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(MinecraftContractError) as raised:
                    canonicalize_game_intent(payload)
                self.assertEqual(raised.exception.code, code)

    def test_blueprint_rejects_absolute_duplicate_dangerous_and_oversized_blocks(self) -> None:
        invalid = (
            ({"action": "place_blueprint", "anchor": "bot", "x": 1, "y": 2, "z": 3, "blocks": []}, "forbidden_absolute_coordinate"),
            (
                {
                    "action": "place_blueprint",
                    "anchor": "bot",
                    "blocks": [
                        {"offset": [1, 0, 0], "block": "cobblestone"},
                        {"offset": [1, 0, 0], "block": "oak_planks"},
                    ],
                },
                "duplicate_blueprint_offset",
            ),
            ({"action": "place_blueprint", "anchor": "bot", "blocks": [{"offset": [1, 0, 0], "block": "tnt"}]}, "dangerous_block_denied"),
            ({"action": "place_blueprint", "anchor": "bot", "blocks": [{"offset": [1, 0, 0], "block": "minecraft:tnt"}]}, "dangerous_block_denied"),
            (
                {
                    "action": "place_blueprint",
                    "anchor": "bot",
                    "blocks": [{"offset": [index, 0, 0], "block": "cobblestone"} for index in range(129)],
                },
                "blueprint_too_large",
            ),
        )
        for intent, code in invalid:
            with self.subTest(code=code):
                with self.assertRaises(MinecraftContractError) as raised:
                    canonicalize_game_intent({"final": True, "source": "text", "intent": intent})
                self.assertEqual(raised.exception.code, code)

    def test_observe_screen_is_a_strict_core_side_read_only_action(self) -> None:
        intent = canonicalize_game_intent({"final": True, "source": "voice", "intent": {"action": "observe_screen"}})
        self.assertEqual(intent, {"action": "observe_screen"})
        with self.assertRaises(MinecraftContractError) as raised:
            canonicalize_game_intent({"final": True, "source": "voice", "intent": {"action": "observe_screen", "question": "上面写了什么"}})
        self.assertEqual(raised.exception.code, "unexpected_intent_field")
        scope = canonicalize_minecraft_scope(SAFE_SCOPE)
        self.assertEqual(check_intent_scope(intent, scope), "")
        self.assertEqual(estimated_world_changes(intent), 0)

    def test_combat_actions_have_strict_schemas_and_players_cannot_be_targets(self) -> None:
        attack = canonicalize_game_intent(
            {"final": True, "source": "voice", "intent": {"action": "attack", "count": 1, "radius": 8, "dimension": "overworld"}}
        )
        self.assertEqual(attack["count"], 1)
        # The schema has no target field at all: a player cannot be expressed.
        with self.assertRaises(MinecraftContractError) as raised:
            canonicalize_game_intent({"final": True, "source": "voice", "intent": {"action": "attack", "player": "Player"}})
        self.assertEqual(raised.exception.code, "unexpected_intent_field")
        for invalid in (
            {"action": "attack", "count": 100},
            {"action": "flee", "distance": 1},
            {"action": "guard", "dimension": "aether"},
        ):
            with self.subTest(intent=invalid):
                with self.assertRaises(MinecraftContractError):
                    canonicalize_game_intent({"final": True, "source": "voice", "intent": invalid})
        scope = canonicalize_minecraft_scope(SAFE_SCOPE)
        self.assertEqual(check_intent_scope(attack, scope), "")
        tight_scope = canonicalize_minecraft_scope({**SAFE_SCOPE, "max_radius": 16})
        out_of_radius = canonicalize_game_intent(
            {"final": True, "source": "voice", "intent": {"action": "attack", "radius": 32, "dimension": "overworld"}}
        )
        self.assertEqual(check_intent_scope(out_of_radius, tight_scope), "radius_out_of_scope")
        for action in (attack, {"action": "flee"}, {"action": "guard"}):
            self.assertEqual(estimated_world_changes(action), 0)

    def test_scope_requires_world_bounds_and_checks_every_mutating_intent(self) -> None:
        scope = canonicalize_minecraft_scope(SAFE_SCOPE)
        allowed = canonicalize_game_intent(
            {"final": True, "source": "text", "intent": {"action": "mine", "block": "oak_log", "count": 2, "radius": 16, "dimension": "overworld"}}
        )
        denied_dimension = canonicalize_game_intent(
            {"final": True, "source": "text", "intent": {"action": "mine", "block": "oak_log", "count": 2, "radius": 16, "dimension": "the_nether"}}
        )
        denied_block = canonicalize_game_intent(
            {"final": True, "source": "text", "intent": {"action": "mine", "block": "diamond_ore", "count": 1, "radius": 16, "dimension": "overworld"}}
        )
        self.assertEqual(check_intent_scope(allowed, scope), "")
        self.assertEqual(check_intent_scope(denied_dimension, scope), "dimension_out_of_scope")
        self.assertEqual(check_intent_scope(denied_block, scope), "block_out_of_scope")
        player_blueprint = canonicalize_game_intent(
            {"final": True, "source": "text", "intent": {"action": "place_blueprint", "anchor": "player", "player": "Stranger", "blocks": [{"offset": [1, 0, 0], "block": "cobblestone"}]}}
        )
        self.assertEqual(check_intent_scope(player_blueprint, scope), "player_out_of_scope")
        nether_scope = canonicalize_minecraft_scope({**SAFE_SCOPE, "dimensions": ["the_nether"]})
        for action in ({"action": "inventory"}, {"action": "craft", "item": "oak_planks", "count": 1}, {"action": "deposit", "items": [{"item": "oak_log", "count": 1}]}):
            intent = canonicalize_game_intent({"final": True, "source": "text", "intent": action})
            self.assertEqual(check_intent_scope(intent, nether_scope), "")
        with self.assertRaises(MinecraftContractError) as raised:
            canonicalize_minecraft_scope({"server_id": "local"})
        self.assertEqual(raised.exception.code, "incomplete_minecraft_scope")


class _FakeRegistry:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.block_allowances: list[object] = []
        self.cancel_calls: list[str] = []
        self.started = False
        self.result: dict[str, object] = {
            "ok": True,
            "verified": True,
            "status": "completed",
            "summary": "done",
            "changes": 0,
            "before": {"dimension": "overworld", "health": 20, "food": 20, "inventory_slots": 2},
            "after": {"dimension": "overworld", "health": 20, "food": 20, "inventory_slots": 2},
        }

    def start_minecraft_session(self, session_id: str, mode: str, scope: dict[str, object], budget: dict[str, object]) -> dict[str, object]:
        self.started = True
        return {"ok": True, "capabilities": ["observe", "mine"]}

    def submit_minecraft_goal(
        self,
        session_id: str,
        goal_id: str,
        intent: dict[str, object],
        *,
        cancel_requested: object = None,
        on_registered: object = None,
        on_submitted: object = None,
        block_allowance: int | None = None,
    ) -> dict[str, object]:
        if callable(cancel_requested) and cancel_requested():
            return {"ok": False, "error": "goal_cancelled", "status": "cancelled", "verified": False}
        if callable(on_registered):
            on_registered()
        if callable(on_submitted):
            on_submitted()
        self.submit_calls += 1
        self.block_allowances.append(block_allowance)
        return dict(self.result)

    def pause_minecraft_goal(self, session_id: str, goal_id: str) -> dict[str, object]:
        return {"ok": True, "acknowledged": True}

    def resume_minecraft_goal(self, session_id: str, goal_id: str) -> dict[str, object]:
        return {"ok": True, "acknowledged": True}

    def cancel_minecraft_goal(self, session_id: str, goal_id: str) -> dict[str, object]:
        self.cancel_calls.append(goal_id)
        return {"ok": True, "acknowledged": True}

    def stop_minecraft_session(self, session_id: str) -> dict[str, object]:
        return {"ok": True, "acknowledged": True}

    def minecraft_session_status(self, session_id: str) -> dict[str, object]:
        return {"ok": True, "state": "ready", "active_goal": False}

    def minecraft_snapshot(self, session_id: str) -> dict[str, object]:
        return {
            "ok": True,
            "observation": {
                "dimension": "overworld",
                "health": 20,
                "food": 18,
                "inventory_slots": 2,
                "inventory_total": 20,
                "world": {"time_of_day": 6000, "raining": False, "entities": []},
            },
            "checkpoint": {},
        }

    def shutdown(self) -> None:
        return None


class MinecraftCoreGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        self.store = CollaborationStore(self.workspace, data_home=Path(self.temporary.name) / "data")
        self.registry = _FakeRegistry()
        self.service = MinecraftGameService(self.store, self.registry)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _start(self, **scope_changes: object) -> str:
        scope = {**SAFE_SCOPE, **scope_changes}
        budget = {"max_steps": 20, "max_seconds": 60, "max_failures": 2}
        preview = self.service.start_session({"mode": "companion", "scope": scope, "budget": budget})
        self.assertTrue(preview["requires_approval"], preview)
        result = self.service.start_session({"mode": "companion", "scope": scope, "budget": budget, "confirmed_scope": True, "approval_id": preview["approval_id"]})
        self.assertTrue(result["ok"], result)
        return str(result["session"]["id"])

    def test_scope_approval_is_digest_bound_single_use_and_expires_closed(self) -> None:
        preview = self.service.start_session({"mode": "companion", "scope": SAFE_SCOPE, "budget": {"max_steps": 2}})
        changed = {**SAFE_SCOPE, "max_radius": 64}
        mismatch = self.service.start_session(
            {"mode": "companion", "scope": changed, "budget": {"max_steps": 2}, "confirmed_scope": True, "approval_id": preview["approval_id"]}
        )
        self.assertEqual(mismatch["error"], "minecraft_scope_approval_invalid")
        replay = self.service.start_session(
            {"mode": "companion", "scope": SAFE_SCOPE, "budget": {"max_steps": 2}, "confirmed_scope": True, "approval_id": preview["approval_id"]}
        )
        self.assertEqual(replay["error"], "minecraft_scope_approval_invalid")

    def _submit(self, session_id: str, goal_id: str, intent: dict[str, object], **changes: object) -> dict[str, object]:
        return self.service.submit_goal(
            {
                "session_id": session_id,
                "goal_id": goal_id,
                "final": changes.pop("final", True),
                "source": changes.pop("source", "voice"),
                "intent": intent,
                **changes,
            }
        )

    def _service_with_screen(self, screen_cache: object) -> MinecraftGameService:
        service = MinecraftGameService(self.store, self.registry, screen_cache=screen_cache)  # type: ignore[arg-type]
        scope = SAFE_SCOPE
        budget = {"max_steps": 20, "max_seconds": 60, "max_failures": 2}
        preview = service.start_session({"mode": "companion", "scope": scope, "budget": budget})
        self.assertTrue(preview["requires_approval"], preview)
        started = service.start_session(
            {"mode": "companion", "scope": scope, "budget": budget, "confirmed_scope": True, "approval_id": preview["approval_id"]}
        )
        self.assertTrue(started["ok"], started)
        self.service = service
        return str(started["session"]["id"])

    def test_observe_screen_runs_core_side_without_bridge_io_and_keeps_receipts(self) -> None:
        class ScreenCache:
            def refresh(self) -> dict[str, object]:
                return {"ok": True, "text": "屏幕摘要：画面是一片橡树林。", "source": "screen", "error": ""}

        session_id = self._service_with_screen(ScreenCache())
        goal = self._submit(session_id, "goal-screen", {"action": "observe_screen"})
        self.assertTrue(goal["ok"], goal)
        self.assertEqual(goal["status"], "completed")
        self.assertIn("橡树林", goal["observation"])
        self.assertEqual(self.registry.submit_calls, 0)
        receipts = self.store.list_receipts(session_id)
        self.assertEqual(len(receipts), 1)
        self.assertTrue(receipts[0]["verification"]["verified"])
        self.assertEqual(self.service._runtime[session_id]["actions_reserved"], 1)

    def test_observe_screen_failure_is_a_failed_receipt_without_bridge_io(self) -> None:
        class BrokenScreenCache:
            def refresh(self) -> dict[str, object]:
                return {"ok": False, "text": "", "source": "screen", "error": "screen_observation_unavailable"}

        session_id = self._service_with_screen(BrokenScreenCache())
        goal = self._submit(session_id, "goal-screen-broken", {"action": "observe_screen"})
        self.assertFalse(goal["ok"])
        self.assertEqual(goal["status"], "failed")
        self.assertEqual(goal["error"], "screen_observation_unavailable")
        self.assertEqual(goal["observation"], "")
        self.assertEqual(self.registry.submit_calls, 0)
        self.assertEqual(self.store.list_receipts(session_id)[0]["status"], "failed")

    def test_observe_screen_without_cache_fails_closed(self) -> None:
        self.service = MinecraftGameService(self.store, self.registry)
        session_id = self._start()
        goal = self._submit(session_id, "goal-screen-nocache", {"action": "observe_screen"})
        self.assertFalse(goal["ok"])
        self.assertEqual(goal["error"], "screen_observation_unavailable")
        self.assertEqual(self.registry.submit_calls, 0)

    def _submit_autonomy(self, session_id: str, goal_id: str, intent: dict[str, object]) -> dict[str, object]:
        return self.service.submit_goal(
            {
                "session_id": session_id,
                "goal_id": goal_id,
                "final": True,
                "source": "voice",
                "intent": intent,
            },
            autonomy=True,
        )

    def test_user_goal_preempts_an_active_autonomy_goal(self) -> None:
        session_id = self._start()
        with self.service._lock:
            self.service._runtime[session_id]["active_goal"] = "autonomy-goal-1"
            self.service._runtime[session_id]["active_goal_autonomy"] = True
        goal = self._submit(session_id, "goal-user", {"action": "inventory"})
        self.assertTrue(goal["ok"], goal)
        self.assertEqual(self.registry.cancel_calls, ["autonomy-goal-1"])
        self.assertEqual(self.service._runtime[session_id]["active_goal"], "")

    def test_autonomy_goal_skips_while_any_goal_is_active(self) -> None:
        session_id = self._start()
        first = self._submit_autonomy(session_id, "goal-autonomy", {"action": "observe"})
        self.assertTrue(first["ok"], first)
        # A goal is only active while its submission is in flight; simulate one.
        with self.service._lock:
            self.service._runtime[session_id]["active_goal"] = "goal-user-busy"
        skipped = self._submit_autonomy(session_id, "goal-autonomy-2", {"action": "inventory"})
        self.assertFalse(skipped["ok"])
        self.assertEqual(skipped["error"], "minecraft_goal_already_running")
        self.assertTrue(skipped["zero_actions"])

    def test_a_cancelled_goal_is_not_a_failure(self) -> None:
        """Interrupting Joi must not spend her failure budget.

        A preempted autonomy goal and a cancelled plan step both come back from
        the bridge as `cancelled`. Counting those as failures meant three
        interruptions paused the session the user was still playing in.
        """

        session_id = self._start()
        runtime = self.service._runtime[session_id]
        before = runtime["failures"]
        self.registry.result = {
            "ok": False,
            "status": "cancelled",
            "verified": False,
            "changes": 0,
            "effects": 0,
            "before": {},
            "after": {},
        }
        result = self._submit(session_id, "goal-cancelled", {"action": "inventory"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.service._runtime[session_id]["failures"], before)
        self.assertEqual(str(self.store.session_payload(session_id).get("state")), "running")

    def test_autonomy_attack_is_forbidden_at_the_service_layer(self) -> None:
        session_id = self._start()
        goal = self._submit_autonomy(session_id, "goal-autonomy-attack", {"action": "attack", "count": 1, "radius": 8, "dimension": "overworld"})
        self.assertFalse(goal["ok"])
        self.assertEqual(goal["error"], "autonomy_attack_forbidden")
        self.assertTrue(goal["zero_actions"])
        self.assertEqual(self.registry.submit_calls, 0)
        self.assertEqual(self.store.list_receipts(session_id), [])

    def _service_with_planner(self) -> tuple[MinecraftGameService, str]:
        def compiler(prompt: str) -> str:
            if "聊天指令" in prompt:
                return '{"intent":{"action":"observe"}}'
            return '{"summary":"两步计划","steps":[{"action":"observe"},{"action":"inventory"}]}'

        service = MinecraftGameService(self.store, self.registry, plan_compiler=compiler)  # type: ignore[arg-type]
        scope = SAFE_SCOPE
        budget = {"max_steps": 20, "max_seconds": 60, "max_failures": 2}
        preview = service.start_session({"mode": "companion", "scope": scope, "budget": budget})
        started = service.start_session(
            {"mode": "companion", "scope": scope, "budget": budget, "confirmed_scope": True, "approval_id": preview["approval_id"]}
        )
        self.assertTrue(started["ok"], started)
        self.service = service
        return service, str(started["session"]["id"])

    def test_plan_preview_requires_approval_then_executes_steps_with_receipts(self) -> None:
        service, session_id = self._service_with_planner()
        preview = service.plan({"session_id": session_id, "goal_text": "先观察再整理"})
        self.assertTrue(preview["ok"], preview)
        self.assertTrue(preview["requires_approval"])
        self.assertEqual(len(preview["steps"]), 2)
        self.assertEqual(preview["estimated_actions"], 2)
        executed = service.execute_plan(
            {"session_id": session_id, "plan_id": preview["plan_id"], "approval_id": preview["approval_id"], "confirmed": True}
        )
        self.assertTrue(executed["ok"], executed)
        deadline = time.monotonic() + 3
        state = "running"
        while time.monotonic() < deadline:
            state = str(service.plan_status({"session_id": session_id, "plan_id": preview["plan_id"]})["state"])
            if state in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.02)
        self.assertEqual(state, "completed")
        self.assertEqual(self.registry.submit_calls, 2)
        self.assertEqual(len(self.store.list_receipts(session_id)), 2)

    def test_plan_approval_is_single_use_and_digest_bound(self) -> None:
        service, session_id = self._service_with_planner()
        preview = service.plan({"session_id": session_id, "goal_text": "目标"})
        executed = service.execute_plan(
            {"session_id": session_id, "plan_id": preview["plan_id"], "approval_id": preview["approval_id"], "confirmed": True}
        )
        self.assertTrue(executed["ok"], executed)
        replay = service.execute_plan(
            {"session_id": session_id, "plan_id": preview["plan_id"], "approval_id": preview["approval_id"], "confirmed": True}
        )
        self.assertFalse(replay["ok"])
        self.assertEqual(replay["error"], "plan_approval_invalid")
        wrong_plan = service.execute_plan(
            {"session_id": session_id, "plan_id": "plan-other", "approval_id": "minecraft-plan-approval-other", "confirmed": True}
        )
        self.assertFalse(wrong_plan["ok"])

    def test_a_finished_session_leaves_no_per_session_bookkeeping_behind(self) -> None:
        """Receipts are the record; in-flight bookkeeping is not.

        Only ``_runtime`` used to be released on stop, so every finished session
        left its goals -- each holding a full result payload with a session
        snapshot and a receipt -- its plans and its chat throttle in memory for
        as long as Core ran.
        """

        service, session_id = self._service_with_planner()
        preview = service.plan({"session_id": session_id, "goal_text": "先观察再整理"})
        service.execute_plan(
            {"session_id": session_id, "plan_id": preview["plan_id"], "approval_id": preview["approval_id"], "confirmed": True}
        )
        service.handle_chat(session_id, "Player", "帮我看看周围")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = str(service.plan_status({"session_id": session_id, "plan_id": preview["plan_id"]})["state"])
            if state in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.02)
        self.assertTrue(service._goals)
        self.assertTrue(service._plans)
        self.assertTrue(service._chat_last_accepted)
        service.stop_session({"session_id": session_id})
        self.assertEqual([key for key in service._goals if key[0] == session_id], [])
        self.assertEqual([key for key in service._plans if key[0] == session_id], [])
        self.assertNotIn(session_id, service._chat_last_accepted)
        self.assertNotIn(session_id, service._runtime)
        self.assertEqual(len(service._goal_order), len(service._goals))
        # The durable record survives the cleanup.
        self.assertTrue(self.store.list_receipts(session_id))

    def test_stopping_the_session_stops_a_running_plan(self) -> None:
        """A plan is not allowed to keep stepping into a world that is gone."""

        service, session_id = self._service_with_planner()
        preview = service.plan({"session_id": session_id, "goal_text": "先观察再整理"})
        with self.subTest("cancelled at the next step boundary"):
            service.execute_plan(
                {"session_id": session_id, "plan_id": preview["plan_id"], "approval_id": preview["approval_id"], "confirmed": True}
            )
            service.stop_session({"session_id": session_id})
            deadline = time.monotonic() + 3
            state = "running"
            while time.monotonic() < deadline:
                row = service._plans.get((session_id, preview["plan_id"]))
                if row is None:
                    state = "forgotten"
                    break
                state = str(row["state"])
                if state in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.02)
            self.assertIn(state, {"forgotten", "cancelled", "failed", "completed"})
        # Whatever it managed before the stop, nothing was submitted afterwards.
        submitted_after_stop = self.registry.submit_calls
        time.sleep(0.15)
        self.assertEqual(self.registry.submit_calls, submitted_after_stop)

    def test_goal_replay_protection_is_bounded_for_a_long_autonomous_session(self) -> None:
        service, session_id = self._service_with_planner()
        for index in range(600):
            service._remember_goal((session_id, f"goal-{index}"), {"digest": str(index), "result": None})
        self.assertLessEqual(len(service._goals), 512)
        self.assertEqual(len(service._goal_order), len(service._goals))
        # Duplicates arrive within seconds of the original, so the recent goals
        # are the ones that must stay protected.
        self.assertIn((session_id, "goal-599"), service._goals)
        self.assertNotIn((session_id, "goal-0"), service._goals)

    def test_the_service_can_name_its_own_live_sessions(self) -> None:
        service, session_id = self._service_with_planner()
        self.assertEqual(service.active_session_ids(), [session_id])
        service.stop_session({"session_id": session_id})
        self.assertEqual(service.active_session_ids(), [])

    def test_one_conversation_card_can_answer_a_compiled_plan(self) -> None:
        service, session_id = self._service_with_planner()
        preview = service.plan({"session_id": session_id, "goal_text": "先观察再整理"})
        approval_id = str(preview["approval_id"])
        self.assertTrue(service.has_pending_plan_approval(approval_id))
        self.assertFalse(service.has_pending_plan_approval("minecraft-plan-approval-unknown"))
        accepted = service.resolve_plan_approval(approval_id, True)
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(accepted["state"], "running")
        # The one-time binding still holds: answering twice cannot replay it.
        self.assertFalse(service.has_pending_plan_approval(approval_id))
        self.assertFalse(service.resolve_plan_approval(approval_id, True)["ok"])

    def test_a_declined_plan_stops_being_executable(self) -> None:
        service, session_id = self._service_with_planner()
        preview = service.plan({"session_id": session_id, "goal_text": "先观察再整理"})
        declined = service.resolve_plan_approval(str(preview["approval_id"]), False)
        self.assertTrue(declined["ok"], declined)
        self.assertEqual(declined["state"], "cancelled")
        self.assertFalse(service.has_pending_plan_approval(str(preview["approval_id"])))
        replay = service.execute_plan(
            {"session_id": session_id, "plan_id": preview["plan_id"], "approval_id": preview["approval_id"], "confirmed": True}
        )
        self.assertFalse(replay["ok"])
        self.assertEqual(self.registry.submit_calls, 0)

    def test_chat_from_whitelisted_player_compiles_one_action_and_skips_strangers(self) -> None:
        service, session_id = self._service_with_planner()
        queued = service.handle_chat(session_id, "Player", "帮我看看周围")
        self.assertTrue(queued["ok"], queued)
        deadline = time.monotonic() + 3
        while self.registry.submit_calls < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.registry.submit_calls, 1)
        stranger = service.handle_chat(session_id, "Stranger", "帮我看看")
        self.assertEqual(stranger["error"], "chat_player_out_of_scope")
        invalid = service.handle_chat(session_id, "坏名字!", "帮我看看")
        self.assertEqual(invalid["error"], "chat_command_invalid")

    def test_chat_flooding_costs_one_model_call_per_interval(self) -> None:
        # Anyone on the whitelist can type as fast as they like, and every line
        # would otherwise buy a compile call.
        service, session_id = self._service_with_planner()
        self.assertTrue(service.handle_chat(session_id, "Player", "帮我看看周围")["ok"])
        flooded = service.handle_chat(session_id, "Player", "再看一次")
        self.assertFalse(flooded["ok"])
        self.assertEqual(flooded["error"], "chat_command_throttled")

    def test_stop_session_remembers_sanitized_world_memory(self) -> None:
        session_id = self._start()
        self._submit(session_id, "goal-one", {"action": "observe"})
        stopped = self.service.stop_session({"session_id": session_id})
        self.assertTrue(stopped["ok"], stopped)
        summary = self.service.memory.summary("local-survival", "world")
        self.assertIn("overworld", summary)
        self.assertIn("minecraft.observe", summary)
        for forbidden in ("position", "坐标"):
            self.assertNotIn(forbidden, summary)

    def test_partial_scope_denial_and_permission_denial_send_zero_bridge_actions(self) -> None:
        session_id = self._start()
        partial = self._submit(session_id, "goal-partial", {"action": "observe"}, final=False)
        out_of_scope = self._submit(
            session_id,
            "goal-scope",
            {"action": "mine", "block": "diamond_ore", "count": 1, "radius": 8, "dimension": "overworld"},
        )
        with patch.object(self.store, "action_allowed", return_value={"allowed": False, "requires_approval": True}):
            denied = self._submit(session_id, "goal-denied", {"action": "observe"})
        self.assertEqual(partial["error"], "final_transcript_required")
        self.assertEqual(out_of_scope["error"], "block_out_of_scope")
        self.assertEqual(denied["error"], "minecraft_action_not_authorized")
        self.assertEqual(self.registry.submit_calls, 0)
        self.assertEqual(self.store.list_receipts(session_id), [])

    def test_pre_registration_cancellation_is_zero_action(self) -> None:
        session_id = self._start()
        registered = False

        def mark_registered() -> None:
            nonlocal registered
            registered = True

        result = self.service.submit_goal(
            {
                "session_id": session_id,
                "goal_id": "goal-cancel-before-register",
                "final": True,
                "source": "voice",
                "intent": {"action": "observe"},
            },
            cancel_requested=lambda: True,
            on_registered=mark_registered,
        )
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["zero_actions"])
        self.assertFalse(registered)
        self.assertEqual(self.registry.submit_calls, 0)

    def test_post_registration_pre_submit_cancellation_is_linearized_to_zero_action(self) -> None:
        session_id = self._start()
        actual_registry = GameAdapterRegistry(self.workspace, Path(self.temporary.name) / "race-data")
        cancellation = threading.Event()
        cancel_result: dict[str, object] = {}

        class RaceBridge:
            def __init__(self) -> None:
                self.submit_calls = 0

            def submit_goal(self, goal_id: str, intent: dict[str, object], *, block_allowance: int | None = None) -> dict[str, object]:
                self.submit_calls += 1
                return {
                    "ok": True,
                    "status": "completed",
                    "verified": True,
                    "effects": 1,
                    "changes": 0,
                    "before": {"dimension": "overworld", "health": 20, "food": 20, "inventory_slots": 0},
                    "after": {"dimension": "overworld", "health": 20, "food": 20, "inventory_slots": 0},
                }

            def cancel(self, goal_id: str) -> dict[str, object]:
                return {"ok": False, "error": "goal_not_active", "acknowledged": False}

        bridge = RaceBridge()
        actual_registry._minecraft_sessions[session_id] = bridge  # type: ignore[assignment]
        actual_registry._minecraft_states[session_id] = "ready"
        self.service.adapters = actual_registry

        def registered_then_stop() -> None:
            cancellation.set()
            cancel_result.update(actual_registry.cancel_minecraft_goal(session_id, "goal-register-race"))

        result = self.service.submit_goal(
            {
                "session_id": session_id,
                "goal_id": "goal-register-race",
                "final": True,
                "source": "voice",
                "intent": {"action": "observe"},
            },
            cancel_requested=cancellation.is_set,
            on_registered=registered_then_stop,
        )

        self.assertEqual(cancel_result.get("error"), "goal_not_active")
        self.assertEqual(result.get("status"), "cancelled")
        self.assertTrue(result.get("zero_actions"))
        self.assertEqual(bridge.submit_calls, 0)
        self.assertEqual(result.get("effects", 0), 0)
        self.assertEqual(self.store.list_receipts(session_id), [])
        self.assertEqual(self.service._runtime[session_id]["actions_reserved"], 0)

    def test_legacy_combined_rpc_never_connects_for_partial_or_non_dry_minecraft(self) -> None:
        bridge = JsonRpcBridge.__new__(JsonRpcBridge)
        result = bridge.game_adapter_run_command(
            {
                "adapter_id": "minecraft",
                "mode": "companion",
                "dry_run": False,
                "final": False,
                "source": "voice",
                "intent": {"action": "observe"},
            }
        )
        self.assertEqual(result["error"], "persistent_session_rpc_required")
        self.assertTrue(result["zero_actions"])

    def test_budget_is_reserved_before_send_and_duplicate_goal_never_reexecutes(self) -> None:
        session_id = self._start(max_actions=1)
        first = self._submit(session_id, "goal-one", {"action": "observe"})
        replay = self._submit(session_id, "goal-one", {"action": "observe"})
        conflict = self._submit(session_id, "goal-one", {"action": "inventory"})
        exhausted = self._submit(session_id, "goal-two", {"action": "inventory"})
        self.assertTrue(first["ok"], first)
        self.assertTrue(replay["replayed"])
        self.assertEqual(conflict["error"], "goal_id_conflict")
        self.assertEqual(exhausted["error"], "action_budget_exhausted")
        self.assertEqual(self.registry.submit_calls, 1)
        self.assertEqual(len(self.store.list_receipts(session_id)), 1)

    def test_the_block_budget_is_charged_what_the_world_reports_not_what_was_estimated(self) -> None:
        """A route that digs its way to the work spends the user's ceiling too.

        Only what an action sets out to change can be estimated in advance. The
        pathfinder breaks and places blocks of its own getting there, so a
        budget settled against the estimate stopped bounding the larger half of
        what actually changed.
        """

        session_id = self._start(max_blocks_changed=10)
        self.registry.result = {
            **self.registry.result,
            "changes": 2,
            "effects": 2,
            # Two blocks mined; eight more broken and placed on the way.
            "world_changes": 10,
        }
        first = self._submit(session_id, "goal-mine", {"action": "mine", "block": "oak_log", "count": 2})
        self.assertTrue(first["ok"], first)
        receipt = self.store.list_receipts(session_id)[0]
        self.assertEqual(receipt["verification"]["blocks_changed"], 10)
        self.assertIn("10 world changes", first["summary"])

        exhausted = self._submit(session_id, "goal-mine-again", {"action": "mine", "block": "oak_log", "count": 1})
        self.assertEqual(exhausted["error"], "block_budget_exhausted")
        self.assertEqual(self.registry.submit_calls, 1)

    def test_each_goal_is_handed_what_is_left_of_the_confirmed_block_budget(self) -> None:
        """The child stops itself at the ceiling instead of reporting it crossed.

        Core refusing the *next* goal is an account of a ceiling already gone
        past: a single route can tunnel for minutes before its receipt arrives.
        """

        session_id = self._start(max_blocks_changed=12)
        self.registry.result = {**self.registry.result, "changes": 3, "effects": 3, "world_changes": 5}
        self._submit(session_id, "goal-one", {"action": "mine", "block": "oak_log", "count": 3})
        self.registry.result = {**self.registry.result, "changes": 1, "effects": 1, "world_changes": 1}
        self._submit(session_id, "goal-two", {"action": "mine", "block": "oak_log", "count": 1})
        # First goal: the whole ceiling. Second: what the first really spent.
        self.assertEqual(self.registry.block_allowances, [12, 7])

    def test_a_read_only_goal_neither_reserves_nor_settles_blocks(self) -> None:
        session_id = self._start(max_blocks_changed=4)
        self.registry.result = {**self.registry.result, "changes": 0, "effects": 0, "world_changes": 0}
        self._submit(session_id, "goal-observe", {"action": "observe"})
        self.registry.result = {**self.registry.result, "changes": 4, "effects": 4, "world_changes": 4}
        mining = self._submit(session_id, "goal-mine", {"action": "mine", "block": "oak_log", "count": 4})
        self.assertTrue(mining["ok"], mining)
        self.assertEqual(self.registry.block_allowances, [4, 4])

    def test_a_bridge_that_reports_no_route_changes_is_charged_its_deliberate_work(self) -> None:
        """An older child only ever counted deliberate work, so that count is all it knows."""

        session_id = self._start(max_blocks_changed=6)
        self.registry.result = {**self.registry.result, "changes": 6, "effects": 6}
        self.registry.result.pop("world_changes", None)
        first = self._submit(session_id, "goal-mine", {"action": "mine", "block": "oak_log", "count": 6})
        self.assertTrue(first["ok"], first)
        self.assertEqual(self.store.list_receipts(session_id)[0]["verification"]["blocks_changed"], 6)
        exhausted = self._submit(session_id, "goal-more", {"action": "mine", "block": "oak_log", "count": 1})
        self.assertEqual(exhausted["error"], "block_budget_exhausted")

    def test_missing_after_state_is_unverified_and_disconnect_result_is_cached_without_replay(self) -> None:
        session_id = self._start()
        self.registry.result = {
            "ok": False,
            "verified": False,
            "status": "unverified",
            "error": "minecraft_disconnected",
            "changes": 1,
            "recovery_required": True,
        }
        first = self._submit(
            session_id,
            "goal-uncertain",
            {"action": "mine", "block": "oak_log", "count": 1, "radius": 8, "dimension": "overworld"},
        )
        replay = self._submit(
            session_id,
            "goal-uncertain",
            {"action": "mine", "block": "oak_log", "count": 1, "radius": 8, "dimension": "overworld"},
        )
        self.assertEqual(first["status"], "unverified")
        self.assertTrue(first["recovery_required"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.registry.submit_calls, 1)
        self.assertEqual(self.store.list_receipts(session_id)[0]["status"], "unverified")
        self.assertEqual(self.store.session_payload(session_id, False)["pause_reason"], "recovery_required")

    def test_live_permission_scope_shrink_is_used_for_each_goal(self) -> None:
        session_id = self._start()
        narrower = canonicalize_minecraft_scope({**SAFE_SCOPE, "allowed_blocks": ["cobblestone"]})
        self.store.grant_permission(session_id, "collaborate", {"game": ["Minecraft"], "minecraft": narrower})
        denied = self._submit(
            session_id,
            "goal-shrunk",
            {"action": "mine", "block": "oak_log", "count": 1, "radius": 8, "dimension": "overworld"},
        )
        self.assertEqual(denied["error"], "block_out_of_scope")
        self.assertEqual(self.registry.submit_calls, 0)

    def test_live_action_budget_shrink_blocks_before_bridge_and_receipt(self) -> None:
        session_id = self._start(max_actions=3)
        first = self._submit(session_id, "goal-before-shrink", {"action": "inventory"})
        self.assertTrue(first["ok"], first)
        narrower = canonicalize_minecraft_scope({**SAFE_SCOPE, "max_actions": 1})
        self.store.grant_permission(session_id, "collaborate", {"game": ["Minecraft"], "minecraft": narrower})
        second = self._submit(session_id, "goal-after-shrink", {"action": "inventory"})
        self.assertEqual(second["error"], "action_budget_exhausted")
        self.assertTrue(second["zero_actions"])
        self.assertEqual(self.registry.submit_calls, 1)
        self.assertEqual(len(self.store.list_receipts(session_id)), 1)

    def test_bridge_verified_flag_without_expected_effect_evidence_is_unverified(self) -> None:
        session_id = self._start()
        self.registry.result = {
            "ok": True,
            "verified": True,
            "status": "completed",
            "changes": 0,
            "effects": 0,
            "before": {"dimension": "overworld", "health": 20, "food": 20, "inventory_slots": 2, "inventory_total": 4},
            "after": {"dimension": "overworld", "health": 20, "food": 20, "inventory_slots": 2, "inventory_total": 4},
        }
        result = self._submit(
            session_id,
            "goal-false-proof",
            {"action": "mine", "block": "oak_log", "count": 1, "radius": 8, "dimension": "overworld"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")

    def test_partial_receipt_has_sanitized_after_state_and_public_result_has_no_private_bridge_data(self) -> None:
        session_id = self._start()
        self.registry.result = {
            "ok": False,
            "verified": False,
            "status": "partial",
            "error": "minecraft_disconnected",
            "changes": 1,
            "effects": 1,
            "recovery_required": True,
            "before": {"dimension": "overworld", "health": 20, "food": 20, "inventory_slots": 2, "inventory_total": 4},
            "after": {"dimension": "overworld", "health": 20, "food": 20, "inventory_slots": 3, "inventory_total": 5},
            "checkpoint": {"position": {"x": 12, "y": 64, "z": -8}},
            "bridge_instance_id": "private-bridge-id",
            "stderr": "private child details",
        }
        result = self._submit(
            session_id,
            "goal-partial-private",
            {"action": "mine", "block": "oak_log", "count": 1, "radius": 8, "dimension": "overworld"},
        )
        public = json.dumps(result, sort_keys=True)
        self.assertNotIn("private-bridge-id", public)
        self.assertNotIn("private child details", public)
        self.assertNotIn('"position"', public)
        receipt = self.store.list_receipts(session_id)[0]
        self.assertEqual(receipt["status"], "partial")
        self.assertNotEqual(receipt["after_summary"], "observation_unavailable")
        self.assertTrue(receipt["verification"]["after_state_present"])

    def test_private_checkpoint_survives_registry_restart_without_public_projection(self) -> None:
        data_home = Path(self.temporary.name) / "checkpoint-data"
        first = GameAdapterRegistry(self.workspace, data_home)
        checkpoint = {
            "dimension": "overworld",
            "health": 20,
            "food": 20,
            "inventory_slots": 1,
            "inventory_total": 1,
            "position": {"x": 1.0, "y": 64.0, "z": 2.0},
            "inventory": [{"name": "oak_log", "count": 1}],
        }
        first._store_minecraft_checkpoint("session-private", "goal-private", checkpoint, "unverified")
        path = first._minecraft_checkpoint_path("session-private")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        second = GameAdapterRegistry(self.workspace, data_home)
        self.assertEqual(second._minecraft_checkpoints["session-private"]["goal_id"], "goal-private")
        public = json.dumps(second.list(), sort_keys=True)
        self.assertNotIn("session-private", public)
        self.assertNotIn("goal-private", public)
        self.assertNotIn('"position"', public)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        second._delete_minecraft_checkpoint("session-private")
        self.assertFalse(path.exists())

    def test_private_connection_profile_is_typed_password_free_and_owner_only(self) -> None:
        data_home = Path(self.temporary.name) / "connection-data"
        registry = GameAdapterRegistry(self.workspace, data_home)
        profile = {
            "host": "127.0.0.1",
            "port": 25565,
            "username": "Joi",
            "auth": "offline",
            "server_id": "local-survival",
            "world": "world",
            "version": "1.21.4",
        }
        result = registry.configure_minecraft_connection(profile)
        self.assertTrue(result["ok"], result)
        self.assertEqual(registry.minecraft_connection_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(registry.minecraft_connection_path.parent.stat().st_mode & 0o777, 0o700)
        public = json.dumps(registry.minecraft_connection_status(), sort_keys=True)
        self.assertNotIn("password", public)
        self.assertNotIn("profiles", public)
        denied = registry.configure_minecraft_connection({**profile, "password": "must-not-be-accepted"})
        self.assertEqual(denied["error"], "minecraft_connection_invalid")

    def test_checkpoint_write_failure_does_not_clear_recovery_required(self) -> None:
        data_home = Path(self.temporary.name) / "checkpoint-failure"
        registry = GameAdapterRegistry(self.workspace, data_home)

        class FailedBridge:
            def submit_goal(self, goal_id: str, intent: dict[str, object], *, block_allowance: int | None = None) -> dict[str, object]:
                return {
                    "ok": False,
                    "status": "unverified",
                    "verified": False,
                    "recovery_required": True,
                    "checkpoint": {"position": {"x": 1, "y": 2, "z": 3}},
                }

        registry._minecraft_sessions["session-write-fail"] = FailedBridge()  # type: ignore[assignment]
        registry._minecraft_states["session-write-fail"] = "ready"
        with patch.object(registry, "_store_minecraft_checkpoint", return_value=False):
            result = registry.submit_minecraft_goal("session-write-fail", "goal-write-fail", {"action": "inventory"})
        self.assertFalse(result["checkpoint_persisted"])
        self.assertTrue(result["recovery_required"])
        self.assertEqual(registry.minecraft_session_status("session-write-fail")["state"], "recovery_required")

    def test_real_checkpoint_io_failure_returns_false_without_losing_the_action_result(self) -> None:
        data_home = Path(self.temporary.name) / "checkpoint-io-failure"
        registry = GameAdapterRegistry(self.workspace, data_home)
        registry.minecraft_checkpoint_dir.parent.mkdir(parents=True)
        registry.minecraft_checkpoint_dir.write_text("not-a-directory", encoding="utf-8")

        class FailedBridge:
            def submit_goal(self, goal_id: str, intent: dict[str, object], *, block_allowance: int | None = None) -> dict[str, object]:
                return {
                    "ok": False,
                    "status": "unverified",
                    "verified": False,
                    "changes": 1,
                    "effects": 1,
                    "recovery_required": True,
                    "checkpoint": {"position": {"x": 1, "y": 2, "z": 3}},
                }

        registry._minecraft_sessions["session-io-fail"] = FailedBridge()  # type: ignore[assignment]
        registry._minecraft_states["session-io-fail"] = "ready"
        result = registry.submit_minecraft_goal("session-io-fail", "goal-io-fail", {"action": "mine"})
        self.assertFalse(result["checkpoint_persisted"])
        self.assertEqual(result["changes"], 1)
        self.assertEqual(registry.minecraft_session_status("session-io-fail")["state"], "recovery_required")


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the bridge contract test")
class MinecraftBridgeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge_script = Path(__file__).parents[1] / "agent_companion" / "adapters" / "minecraft-bridge" / "index.js"
        self.environment = {
            "JOI_MINECRAFT_FAKE": "1",
            "JOI_MINECRAFT_FAKE_DELAY_MS": "5",
            "JOI_MINECRAFT_SERVER_ID": "local-survival",
            "JOI_MINECRAFT_WORLD": "world",
        }
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-test",
            mode="companion",
            scope=canonicalize_minecraft_scope(SAFE_SCOPE),
            budget={"max_steps": 20, "max_seconds": 60},
            environment=self.environment,
        )

    def tearDown(self) -> None:
        self.client.close()

    def test_one_process_handles_multiple_goals_and_returns_verified_receipts(self) -> None:
        started = self.client.start()
        self.assertTrue(started["ok"], started)
        first = self.client.submit_goal("goal-observe", {"action": "observe", "dimension": "overworld", "radius": 8})
        second = self.client.submit_goal("goal-inventory", {"action": "inventory"})
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertTrue(first["verified"])
        self.assertTrue(second["verified"])
        self.assertEqual(first["bridge_instance_id"], second["bridge_instance_id"])

    def test_a_route_that_would_outspend_the_block_budget_stops_at_the_ceiling(self) -> None:
        """The child stops itself, and says why, rather than reporting it overspent."""

        self.client.close()
        environment = {**self.environment, "JOI_MINECRAFT_FAKE_ROUTE_CHANGES": "6"}
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-route-budget",
            mode="companion",
            scope=canonicalize_minecraft_scope(SAFE_SCOPE),
            budget={"max_steps": 20, "max_seconds": 60},
            environment=environment,
        )
        self.assertTrue(self.client.start()["ok"])
        stopped = self.client.submit_goal(
            "goal-route",
            {"action": "mine", "block": "oak_log", "count": 1, "radius": 8, "dimension": "overworld"},
            block_allowance=2,
        )
        self.assertFalse(stopped["ok"], stopped)
        self.assertEqual(stopped["error"], "block_budget_exhausted")
        # The block that crossed the ceiling is the last one it changed.
        self.assertEqual(stopped["world_changes"], 3)
        self.assertEqual(stopped["changes"], 0)

    def test_route_changes_are_reported_when_they_stay_inside_the_allowance(self) -> None:
        self.client.close()
        environment = {**self.environment, "JOI_MINECRAFT_FAKE_ROUTE_CHANGES": "3"}
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-route-report",
            mode="companion",
            scope=canonicalize_minecraft_scope(SAFE_SCOPE),
            budget={"max_steps": 20, "max_seconds": 60},
            environment=environment,
        )
        self.assertTrue(self.client.start()["ok"])
        result = self.client.submit_goal(
            "goal-route-ok",
            {"action": "mine", "block": "oak_log", "count": 2, "radius": 8, "dimension": "overworld"},
            block_allowance=32,
        )
        self.assertTrue(result["ok"], result)
        # Two mined, three broken on the way there.
        self.assertEqual(result["changes"], 2)
        self.assertEqual(result["world_changes"], 5)

    def test_cancel_ack_stops_a_delayed_mutating_goal(self) -> None:
        self.client.close()
        environment = {**self.environment, "JOI_MINECRAFT_FAKE_DELAY_MS": "250"}
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-cancel",
            mode="companion",
            scope=canonicalize_minecraft_scope(SAFE_SCOPE),
            budget={"max_steps": 20, "max_seconds": 60},
            environment=environment,
        )
        self.assertTrue(self.client.start()["ok"])
        import threading

        result: dict[str, object] = {}
        cancellation = threading.Event()
        submitted = threading.Event()
        worker = threading.Thread(
            target=lambda: result.update(
                self.client.submit_goal(
                    "goal-build",
                    {"action": "place_blueprint", "anchor": "bot", "dimension": "overworld", "blocks": [{"offset": [1, 0, 0], "block": "cobblestone"}]},
                    cancel_requested=cancellation.is_set,
                    on_submitted=submitted.set,
                )
            )
        )
        worker.start()
        self.assertTrue(submitted.wait(1))
        cancellation.set()
        cancelled = self.client.cancel("goal-build")
        worker.join(timeout=3)
        self.assertTrue(cancelled["ok"], cancelled)
        self.assertTrue(cancelled["acknowledged"])
        self.assertFalse(worker.is_alive())
        self.assertFalse(bool(result.get("ok")))
        next_goal = self.client.submit_goal("goal-after-cancel", {"action": "inventory"})
        self.assertTrue(next_goal["ok"], next_goal)

    def test_missing_pause_ack_forces_termination_and_never_claims_paused(self) -> None:
        self.client.close()
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-no-ack",
            mode="companion",
            scope=canonicalize_minecraft_scope(SAFE_SCOPE),
            budget={"max_steps": 20, "max_seconds": 60},
            environment={
                **self.environment,
                "JOI_MINECRAFT_FAKE_DELAY_MS": "500",
                "JOI_MINECRAFT_FAKE_IGNORE_CONTROL": "1",
            },
            control_timeout=0.1,
        )
        self.assertTrue(self.client.start()["ok"])
        import threading

        worker = threading.Thread(
            target=lambda: self.client.submit_goal(
                "goal-no-ack",
                {"action": "mine", "block": "oak_log", "count": 1, "radius": 8, "dimension": "overworld"},
            )
        )
        worker.start()
        time.sleep(0.02)
        paused = self.client.pause("goal-no-ack")
        worker.join(timeout=2)
        self.assertFalse(paused["ok"])
        self.assertFalse(paused["acknowledged"])
        self.assertTrue(paused["forced_terminated"])
        self.assertFalse(self.client.alive)
        self.assertFalse(worker.is_alive())

    def test_explicit_control_error_also_forces_termination(self) -> None:
        self.assertTrue(self.client.start()["ok"])
        rejected = self.client.pause("goal-not-running")
        self.assertFalse(rejected["ok"])
        self.assertFalse(rejected["acknowledged"])
        self.assertTrue(rejected["forced_terminated"])
        self.assertFalse(self.client.alive)

    def test_core_giving_up_on_a_slow_goal_cancels_it_and_keeps_the_world(self) -> None:
        """Core's ack window is a backstop, not a kill switch.

        It used to terminate the child, so any task that took longer than the
        window disconnected the bot and took the whole session with it. The
        safety property it was protecting -- no effects landing after Core has
        reported failure -- is now kept by cancelling the goal instead.
        """

        self.client.close()
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-response-timeout",
            mode="companion",
            scope=canonicalize_minecraft_scope(SAFE_SCOPE),
            budget={"max_steps": 20, "max_seconds": 60},
            environment={**self.environment, "JOI_MINECRAFT_FAKE_DELAY_MS": "700"},
            response_timeout=1,
        )
        self.assertTrue(self.client.start()["ok"])
        result = self.client.submit_goal(
            "goal-timeout",
            {"action": "mine", "block": "oak_log", "count": 3, "radius": 8, "dimension": "overworld"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "goal_timeout")
        self.assertFalse(result["recovery_required"])
        self.assertTrue(self.client.alive)
        # The cancelled goal never lands late, and the world is still Joi's to use.
        time.sleep(0.9)
        self.assertTrue(self.client.alive)
        follow_up = self.client.submit_goal("goal-after-timeout", {"action": "inventory"})
        self.assertTrue(follow_up["ok"], follow_up)

    def test_a_slow_action_reports_partial_without_leaving_the_world(self) -> None:
        self.client.close()
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-action-timeout",
            mode="companion",
            scope=canonicalize_minecraft_scope(SAFE_SCOPE),
            budget={"max_steps": 20, "max_seconds": 60},
            environment={
                **self.environment,
                "JOI_MINECRAFT_FAKE_DELAY_MS": "600",
                "JOI_MINECRAFT_ACTION_TIMEOUT_MS": "1000",
            },
            response_timeout=3,
        )
        self.assertTrue(self.client.start()["ok"])
        result = self.client.submit_goal(
            "goal-action-timeout",
            {"action": "mine", "block": "oak_log", "count": 3, "radius": 8, "dimension": "overworld"},
        )
        self.assertFalse(result["ok"])
        # A long task is a long task: the bot stays in the world and the session
        # stays usable. It used to quit Minecraft and exit the process here,
        # which is what ended the voice session on every real request.
        self.assertFalse(result["recovery_required"])
        self.assertIn(result["status"], {"partial", "unverified"})
        self.assertEqual(result["error"], "goal_timeout")
        time.sleep(0.3)
        self.assertTrue(self.client.alive)
        follow_up = self.client.submit_goal("goal-after-action-timeout", {"action": "inventory"})
        self.assertTrue(follow_up["ok"], follow_up)

    def test_intent_dimension_must_match_the_live_dimension(self) -> None:
        self.client.close()
        scope = canonicalize_minecraft_scope({**SAFE_SCOPE, "dimensions": ["overworld", "the_nether"]})
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-dimension",
            mode="companion",
            scope=scope,
            budget={"max_steps": 20, "max_seconds": 60},
            environment=self.environment,
        )
        self.assertTrue(self.client.start()["ok"])
        result = self.client.submit_goal(
            "goal-nether",
            {"action": "mine", "block": "oak_log", "count": 1, "radius": 8, "dimension": "the_nether"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "dimension_out_of_scope")

    def test_idle_disconnect_is_reported_as_recovery_on_the_next_goal(self) -> None:
        self.client.close()
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-idle-disconnect",
            mode="companion",
            scope=canonicalize_minecraft_scope(SAFE_SCOPE),
            budget={"max_steps": 20, "max_seconds": 60},
            environment={**self.environment, "JOI_MINECRAFT_FAKE_IDLE_DISCONNECT_MS": "20"},
        )
        self.assertTrue(self.client.start()["ok"])
        time.sleep(0.05)
        result = self.client.submit_goal("goal-after-idle-disconnect", {"action": "inventory"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["recovery_required"])
        self.assertEqual(result["status"], "unverified")
        self.assertTrue(result["after"])

    def test_child_environment_is_allowlisted(self) -> None:
        self.client.close()
        script = Path(__file__).parent / "fixtures" / "minecraft_env_probe.py"
        with patch.dict(os.environ, {"JOI_SECRET_CANARY": "must-not-leak", "JOI_MINECRAFT_PASSWORD": "must-not-leak"}):
            probe = MinecraftBridgeClient(
                [shutil.which("python3") or "python3", str(script)],
                session_id="session-env",
                mode="companion",
                scope=canonicalize_minecraft_scope(SAFE_SCOPE),
                budget={},
            )
            try:
                ready = probe.read_ready_for_test()
                self.assertEqual(ready["type"], "bridge.ready")
            finally:
                probe.close()

    def test_disconnect_after_uncertain_effect_never_restarts_or_replays(self) -> None:
        self.client.close()
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-disconnect",
            mode="companion",
            scope=canonicalize_minecraft_scope(SAFE_SCOPE),
            budget={"max_steps": 20, "max_seconds": 60},
            environment={**self.environment, "JOI_MINECRAFT_FAKE_DISCONNECT_AFTER_EFFECT": "1"},
        )
        self.assertTrue(self.client.start()["ok"])
        result = self.client.submit_goal(
            "goal-uncertain",
            {"action": "mine", "block": "oak_log", "count": 2, "radius": 8, "dimension": "overworld"},
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["recovery_required"])
        deadline = time.monotonic() + 1
        while self.client.alive and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.client.alive)

    def test_raw_protocol_replays_cached_result_and_rejects_wrong_session_and_sequence_gap(self) -> None:
        self.client.close()
        environment = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "TMPDIR"}}
        environment["JOI_MINECRAFT_FAKE"] = "1"
        environment["JOI_MINECRAFT_SERVER_ID"] = "local-survival"
        environment["JOI_MINECRAFT_WORLD"] = "world"
        process = subprocess.Popen(
            [str(shutil.which("node")), str(self.bridge_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )

        def receive() -> dict[str, object]:
            assert process.stdout is not None
            return json.loads(process.stdout.readline())

        def send(payload: dict[str, object]) -> None:
            assert process.stdin is not None
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()

        try:
            self.assertEqual(receive()["type"], "bridge.ready")
            start = {
                "protocol": "joi.game_adapter",
                "version": 2,
                "type": "session.start",
                "session_id": "session-raw",
                "message_id": "start-1",
                "sequence": 1,
                "payload": {"mode": "companion", "scope": canonicalize_minecraft_scope(SAFE_SCOPE), "budget": {}},
            }
            send(start)
            self.assertEqual(receive()["type"], "session.ready")
            goal = {
                "protocol": "joi.game_adapter",
                "version": 2,
                "type": "goal.submit",
                "session_id": "session-raw",
                "message_id": "goal-1",
                "sequence": 2,
                "goal_id": "goal-build",
                "payload": {
                    "intent": {
                        "action": "place_blueprint",
                        "anchor": "bot",
                        "dimension": "overworld",
                        "blocks": [{"offset": [1, 0, 0], "block": "cobblestone"}],
                    }
                },
            }
            send(goal)
            self.assertEqual(receive()["type"], "goal.accepted")
            completed = receive()
            self.assertEqual(completed["type"], "goal.completed")
            send(goal)
            replayed = receive()
            self.assertEqual(replayed["type"], "goal.completed")
            self.assertTrue(replayed["payload"]["replayed"])
            wrong_session = {
                "protocol": "joi.game_adapter",
                "version": 2,
                "type": "state.snapshot.request",
                "session_id": "session-other",
                "message_id": "snapshot-wrong",
                "sequence": 3,
                "payload": {},
            }
            send(wrong_session)
            self.assertEqual(receive()["payload"]["error"], "bridge_session_mismatch")
            snapshot = {**wrong_session, "session_id": "session-raw", "message_id": "snapshot-ok"}
            send(snapshot)
            observed = receive()
            self.assertEqual(observed["type"], "state.snapshot")
            self.assertEqual(observed["payload"]["checkpoint"]["blocks_changed"], 1)
            gap = {**snapshot, "message_id": "snapshot-gap", "sequence": 5}
            send(gap)
            self.assertEqual(receive()["payload"]["error"], "bridge_sequence_violation")
        finally:
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=2)
            for stream in (process.stdout, process.stderr):
                if stream:
                    stream.close()

    def test_state_snapshot_request_returns_sanitized_live_state(self) -> None:
        started = self.client.start()
        self.assertTrue(started["ok"], started)
        snapshot = self.client.request_snapshot()
        self.assertTrue(snapshot["ok"], snapshot)
        self.assertEqual(
            set(snapshot["observation"]),
            {"dimension", "health", "food", "inventory_slots", "inventory_total", "inventory_items", "world"},
        )
        self.assertEqual(snapshot["observation"]["dimension"], "overworld")
        world = snapshot["observation"]["world"]
        self.assertEqual(world["time_of_day"], 6000)
        self.assertFalse(world["raining"])
        self.assertIsInstance(world["entities"], list)
        checkpoint = snapshot["checkpoint"]
        self.assertEqual(set(checkpoint["position"]), {"x", "y", "z"})
        self.assertEqual(checkpoint["dimension"], "overworld")
        self.assertTrue(any(row["name"] == "oak_log" for row in checkpoint["inventory"]))
        self.assertEqual(snapshot["bridge_instance_id"], started["bridge_instance_id"])

    def test_fake_bridge_executes_combat_actions_with_verified_effects(self) -> None:
        started = self.client.start()
        self.assertTrue(started["ok"], started)
        attack = self.client.submit_goal("goal-attack", {"action": "attack", "count": 3, "radius": 8, "dimension": "overworld"})
        self.assertTrue(attack["ok"], attack)
        self.assertEqual(attack["effects"], 3)
        self.assertTrue(attack["verified"])
        flee = self.client.submit_goal("goal-flee", {"action": "flee", "distance": 12, "duration_seconds": 1, "dimension": "overworld"})
        self.assertTrue(flee["ok"], flee)
        self.assertEqual(flee["effects"], 1)
        guard = self.client.submit_goal("goal-guard", {"action": "guard", "dimension": "overworld"})
        self.assertTrue(guard["ok"], guard)
        self.assertEqual(guard["effects"], 1)

    def test_fake_bridge_emits_whitelisted_chat_lines(self) -> None:
        self.client.close()
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-chat",
            mode="companion",
            scope=canonicalize_minecraft_scope(SAFE_SCOPE),
            budget={"max_steps": 20, "max_seconds": 60},
            environment={
                **self.environment,
                "JOI_MINECRAFT_FAKE_CHAT_LINES": json.dumps(
                    [{"player": "Player", "text": "帮我挖点木头"}, {"player": "Player", "text": "注意安全"}]
                ),
            },
        )
        received: list[dict[str, object]] = []
        disposer = self.client.add_event_listener(received.append)
        self.assertTrue(self.client.start()["ok"])
        deadline = time.monotonic() + 6
        while sum(1 for event in received if event.get("type") == "chat.observed") < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        chats = [event for event in received if event.get("type") == "chat.observed"]
        self.assertEqual(len(chats), 2)
        self.assertEqual(chats[0]["payload"], {"player": "Player", "text": "帮我挖点木头"})
        self.assertEqual(chats[0]["session_id"], "session-chat")
        self.assertEqual(chats[0]["reply_to"], "")
        disposer()

    def test_unsolicited_events_reach_listeners_and_disposer_stops_them(self) -> None:
        self.client.close()
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-push",
            mode="companion",
            scope=canonicalize_minecraft_scope(SAFE_SCOPE),
            budget={"max_steps": 20, "max_seconds": 60},
            environment={**self.environment, "JOI_MINECRAFT_FAKE_PUSH_SNAPSHOT_MS": "40"},
        )
        received: list[dict[str, object]] = []
        disposer = self.client.add_event_listener(received.append)
        self.assertTrue(self.client.start()["ok"])
        deadline = time.monotonic() + 6
        while sum(1 for event in received if event.get("type") == "state.snapshot") < 3 and time.monotonic() < deadline:
            time.sleep(0.05)
        snapshots = [event for event in received if event.get("type") == "state.snapshot"]
        self.assertGreaterEqual(len(snapshots), 3)
        self.assertEqual(snapshots[0]["session_id"], "session-push")
        self.assertEqual(snapshots[0]["reply_to"], "")
        disposer()
        time.sleep(0.2)
        settled = len(received)
        time.sleep(0.2)
        self.assertEqual(len(received), settled)
        # The buffered event window is bounded: consumed replies and old
        # unsolicited events are evicted instead of leaking.
        self.assertLessEqual(len(self.client._events), 512)
        self.assertLessEqual(len(self.client._seen_output), 512)

    def test_combat_events_and_nearby_hostiles_flow_to_listeners_and_snapshot(self) -> None:
        self.client.close()
        self.client = MinecraftBridgeClient(
            [str(shutil.which("node")), str(self.bridge_script)],
            session_id="session-combat",
            mode="companion",
            scope=canonicalize_minecraft_scope(SAFE_SCOPE),
            budget={"max_steps": 20, "max_seconds": 60},
            environment={
                **self.environment,
                "JOI_MINECRAFT_FAKE_PUSH_SNAPSHOT_MS": "40",
                "JOI_MINECRAFT_FAKE_COMBAT_MS": "40",
            },
        )
        received: list[dict[str, object]] = []
        disposer = self.client.add_event_listener(received.append)
        self.assertTrue(self.client.start()["ok"])
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if any(event.get("type") == "combat.started" for event in received) and any(
                event.get("type") == "combat.ended" for event in received
            ):
                break
            time.sleep(0.05)
        self.assertTrue(any(event.get("type") == "combat.started" for event in received))
        self.assertTrue(any(event.get("type") == "combat.ended" for event in received))
        combat = next(event for event in received if event.get("type") == "combat.started")
        self.assertEqual(combat["payload"]["state"], "active")
        self.assertEqual(combat["reply_to"], "")
        # While combat is active the sanitized observation carries hostiles.
        hostile_seen = False
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            snapshot = self.client.request_snapshot()
            hostiles = snapshot.get("observation", {}).get("nearby_hostiles") or []
            if hostiles:
                hostile_seen = True
                self.assertEqual(hostiles[0], {"name": "zombie", "count": 2})
                break
            time.sleep(0.02)
        self.assertTrue(hostile_seen)
        disposer()

    def test_registry_forwards_unsolicited_events_and_serves_snapshots(self) -> None:
        with patch.dict(
            os.environ,
            {
                "JOI_MINECRAFT_HOST": "127.0.0.1",
                "JOI_MINECRAFT_PORT": "25565",
                "JOI_MINECRAFT_USERNAME": "Joi",
                "JOI_MINECRAFT_AUTH": "offline",
                "JOI_MINECRAFT_FAKE": "1",
                "JOI_MINECRAFT_FAKE_DELAY_MS": "5",
                "JOI_MINECRAFT_FAKE_PUSH_SNAPSHOT_MS": "40",
                "JOI_MINECRAFT_FAKE_COMBAT_MS": "40",
                "JOI_MINECRAFT_SERVER_ID": "local-survival",
                "JOI_MINECRAFT_WORLD": "world",
            },
            clear=False,
        ):
            with tempfile.TemporaryDirectory() as temporary:
                registry = GameAdapterRegistry(Path(__file__).parents[1], Path(temporary) / "data")
                registry._state["minecraft"] = {"installed": True, "enabled": True, "installed_at": time.time()}
                started = registry.start_minecraft_session(
                    "session-registry-events", "companion", canonicalize_minecraft_scope(SAFE_SCOPE), {}
                )
        self.assertTrue(started["ok"], started)
        try:
            deadline = time.monotonic() + 6
            events: list[dict[str, object]] = []
            while time.monotonic() < deadline:
                events = registry.minecraft_session_events("session-registry-events")
                if any(event.get("type") == "combat.started" for event in events) and len(events) >= 4:
                    break
                time.sleep(0.05)
            self.assertGreaterEqual(len(events), 4)
            types = {event.get("type") for event in events}
            self.assertIn("state.snapshot", types)
            self.assertIn("combat.started", types)
            self.assertIn("combat.ended", types)
            self.assertEqual(events[0]["session_id"], "session-registry-events")
            snapshot = registry.minecraft_snapshot("session-registry-events")
            self.assertTrue(snapshot["ok"], snapshot)
            # Combat may be active or clear at this instant; hostiles must be
            # a sanitized name+count list when present and never coordinates.
            hostiles = snapshot["observation"].get("nearby_hostiles") or []
            for row in hostiles:
                self.assertEqual(set(row), {"name", "count"})
            missing = registry.minecraft_snapshot("session-not-found")
            self.assertEqual(missing["error"], "minecraft_session_not_found")
        finally:
            registry.shutdown()


if __name__ == "__main__":
    unittest.main()
