from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_companion.core.capability_orchestrator import ComputerUseOrchestrator
from agent_companion.core.collaboration_store import CollaborationStore
from agent_companion.core.game_adapters import GameAdapterRegistry
from agent_companion.core.ok_ww import ok_ww_runner_path, ok_ww_setup_hint
from agent_companion.core.vision.target_evidence import CaptureIdentity, TargetEvidence


def _evidence(*, app_id: str = "", domain: str = "", path: str = "", source: str = "accessibility", geometry_trusted: bool = True, observed_at: float | None = None) -> TargetEvidence:
    """Evidence that is current and actionable unless a test says otherwise."""
    return TargetEvidence(
        target_id="t-1",
        label="下一页",
        source=source,
        confidence=0.9,
        identity=CaptureIdentity(display_id="1", window_id="w-1", app_id=app_id, scale=2.0, capture_digest="cap-1", geometry_trusted=geometry_trusted),
        clickable=True,
        enabled=True,
        logical_bounds=(10, 10, 40, 30),
        domain=domain,
        path=path,
        observed_at=time.time() if observed_at is None else observed_at,
    )
from agent_companion.core.scene_session import SceneSession
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, ToolRequest, VoiceLine
from agent_companion.core.action_intent import ActionIntent, EffectKind
from agent_companion.core.server import _request_within_bound_scope, _sensitive_request_signals


class CapabilityPlatformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        self.store = CollaborationStore(self.workspace, data_home=Path(self.temporary.name) / "data")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_budget_and_repeated_noop_pause_session(self) -> None:
        session = self.store.start_session("computer_use", "test", "collaborate", budget={"max_steps": 20, "max_failures": 8})["session"]
        orchestrator = ComputerUseOrchestrator(self.workspace, self.store)
        for index in range(3):
            event = AgentEvent(
                EventType.TOOL_COMPLETED,
                f"task-{index}",
                DisplayCard("电脑操作", "没有明显变化"),
                VoiceLine(""),
                {
                    "tool": "computer.click",
                    "computer_use": {"observation": {"title": "Safari", "width": 1200, "height": 800}},
                    "post_action_verification": {"status": "likely_noop", "signals": {"image_changed": False}},
                },
            )
            result = orchestrator.record_tool_event(session["id"], event)
        self.assertTrue(result["paused"])
        self.assertEqual(result["pause_reason"], "repeated_no_change")
        self.assertEqual(self.store.session_payload(session["id"], False)["state"], "paused")

    def test_scope_and_sensitive_boundaries(self) -> None:
        scope = {"directory": [str(self.workspace)], "application": ["Safari"], "domain": ["example.com"], "game": ["Minecraft"]}
        self.assertTrue(_request_within_bound_scope(ToolRequest("computer.open_app", {"app_name": "Safari"}), scope))
        self.assertTrue(_request_within_bound_scope(ToolRequest("computer.workflow", {"url": "https://docs.example.com/a"}), scope))
        self.assertFalse(_request_within_bound_scope(ToolRequest("computer.workflow", {"url": "https://unbound.test"}), scope))

    def test_a_coordinate_click_needs_evidence_not_merely_some_binding(self) -> None:
        scope = {"directory": [str(self.workspace)], "application": ["Safari"], "domain": ["example.com"], "game": []}
        click = ToolRequest("computer.click", {"x": 10, "y": 20}, "点击候选目标：下一页")

        # The arguments name nothing, so without evidence the step cannot be
        # placed inside the binding and must go to approval.
        self.assertFalse(_request_within_bound_scope(click, scope, None))

        in_scope = _evidence(app_id="Safari")
        self.assertTrue(_request_within_bound_scope(click, scope, in_scope))

        # Same click, evidence gathered in an application nobody bound.
        self.assertFalse(_request_within_bound_scope(click, scope, _evidence(app_id="Mail")))

    def test_evidence_that_is_no_longer_current_does_not_authorize(self) -> None:
        scope = {"application": ["Safari"]}
        click = ToolRequest("computer.click", {"x": 10, "y": 20}, "点击")
        self.assertFalse(_request_within_bound_scope(click, scope, _evidence(app_id="Safari", observed_at=time.time() - 600)))
        self.assertFalse(_request_within_bound_scope(click, scope, _evidence(app_id="Safari", source="vision")))
        self.assertFalse(_request_within_bound_scope(click, scope, _evidence(app_id="Safari", geometry_trusted=False)))

    def test_bound_domain_and_directory_also_place_a_click_in_scope(self) -> None:
        scope = {"domain": ["example.com"], "directory": [str(self.workspace)]}
        click = ToolRequest("computer.click", {"x": 1, "y": 1}, "点击")
        self.assertTrue(_request_within_bound_scope(click, scope, _evidence(domain="docs.example.com")))
        self.assertTrue(_request_within_bound_scope(click, scope, _evidence(path=str(self.workspace / "notes.md"))))
        self.assertFalse(_request_within_bound_scope(click, scope, _evidence(domain="unbound.test")))

    def test_an_action_without_an_after_observation_is_not_completed(self) -> None:
        session = self.store.start_session("computer_use", "点击", "collaborate")["session"]
        orchestrator = ComputerUseOrchestrator(self.workspace, self.store)

        blind = AgentEvent(
            EventType.TOOL_COMPLETED,
            "task-blind",
            DisplayCard("电脑操作", "已点击"),
            VoiceLine(""),
            {"tool": "computer.click", "computer_use": {"before_title": "Safari"}},
        )
        orchestrator.record_tool_event(session["id"], blind)
        self.assertEqual(self.store.list_receipts(session["id"])[-1]["status"], "unverified")

        observed = AgentEvent(
            EventType.TOOL_COMPLETED,
            "task-observed",
            DisplayCard("电脑操作", "已点击"),
            VoiceLine(""),
            {
                "tool": "computer.click",
                "computer_use": {"before_title": "Safari", "observation": {"title": "Safari", "width": 100, "height": 100}},
                "post_action_verification": {"status": "changed"},
            },
        )
        orchestrator.record_tool_event(session["id"], observed)
        self.assertEqual(self.store.list_receipts(session["id"])[-1]["status"], "completed")

    def test_effect_kind_comes_from_the_tool_not_its_wording(self) -> None:
        # Typed red lines hold regardless of how the step is described.
        for tool, effect in (
            ("files.delete", EffectKind.DELETION),
            ("package.install", EffectKind.INSTALLATION),
            ("git.push", EffectKind.EXTERNAL_SEND),
            ("external.launch_admin", EffectKind.PERMISSION_EXPANSION),
        ):
            intent = ActionIntent.from_request(ToolRequest(tool, {}, "例行整理，无需在意"))
            self.assertEqual(intent.effect_kind, effect)
            self.assertTrue(intent.is_red_line)
            self.assertEqual(intent.sensitivity, "red_line")
            # Reassuring prose must not be able to talk a typed red line down.
            self.assertEqual(intent.escalated_by, "")

        observation = ActionIntent.from_request(ToolRequest("observe.screen", {}, "看一眼"))
        self.assertEqual(observation.effect_kind, EffectKind.NONE)
        self.assertFalse(observation.is_red_line)

        # An unregistered tool is not assumed harmless.
        self.assertFalse(ActionIntent.from_request(ToolRequest("some.new_tool", {})).effect_kind == EffectKind.NONE)

    def test_wording_can_escalate_an_ordinary_tool_but_never_the_reverse(self) -> None:
        click = ActionIntent.from_request(ToolRequest("computer.click", {"x": 5, "y": 5}, "点击候选目标：下一页"))
        self.assertEqual(click.effect_kind, EffectKind.DESKTOP_INPUT)

        # The resolved target label only ever reaches policy through the reason.
        pay = ActionIntent.from_request(ToolRequest("computer.click", {"x": 640, "y": 480}, "点击候选目标：立即支付"))
        self.assertEqual((pay.typed_effect, pay.effect_kind, pay.escalated_by), (EffectKind.DESKTOP_INPUT, EffectKind.PAYMENT, "signal_marker"))

        login = ActionIntent.from_request(ToolRequest("computer.click", {"x": 10, "y": 20}, "点击已选择的候选目标：Sign in with Google"))
        self.assertEqual(login.effect_kind, EffectKind.AUTHENTICATION)

        typed = ActionIntent.from_request(ToolRequest("computer.type_text", {"text": "验证码 480913"}, "输入文字"))
        self.assertEqual(typed.effect_kind, EffectKind.AUTHENTICATION)

    def test_identical_actions_share_an_idempotency_key(self) -> None:
        first = ActionIntent.from_request(ToolRequest("computer.click", {"x": 1, "y": 2}, "点击"))
        same = ActionIntent.from_request(ToolRequest("computer.click", {"y": 2, "x": 1}, "换个说法"))
        other = ActionIntent.from_request(ToolRequest("computer.click", {"x": 1, "y": 3}, "点击"))
        # Key identity follows the effect, not argument order or prose.
        self.assertEqual(first.idempotency_key, same.idempotency_key)
        self.assertNotEqual(first.idempotency_key, other.idempotency_key)
        # Recalled memory rides along on requests and must not shift the key.
        with_context = ActionIntent.from_request(ToolRequest("computer.click", {"x": 1, "y": 2, "memory_context": "…"}, "点击"))
        self.assertEqual(first.idempotency_key, with_context.idempotency_key)

    def test_coordinate_click_onto_sensitive_control_still_needs_confirmation(self) -> None:
        session = self.store.start_session("computer_use", "结账", "delegate")["session"]
        self.store.grant_permission(session["id"], "delegate", {"application": ["Safari"]})

        def verdict(request: ToolRequest) -> dict:
            intent = ActionIntent.from_request(request)
            return self.store.action_allowed(
                session["id"], request.name, "medium",
                signals=_sensitive_request_signals(request),
                effect_kind=intent.effect_kind.value,
            )

        pay = ToolRequest("computer.click", {"x": 640, "y": 480}, "点击候选目标：立即支付")
        self.assertTrue(verdict(pay)["requires_approval"])
        self.assertEqual(verdict(pay)["sensitive_action"], "payment")

        login = ToolRequest("computer.click", {"x": 10, "y": 20}, "点击已选择的候选目标：Sign in with Google")
        self.assertEqual(verdict(login)["sensitive_action"], "authentication")

        typed = ToolRequest("computer.type_text", {"text": "验证码 480913"}, "输入文字")
        self.assertEqual(verdict(typed)["sensitive_action"], "authentication")

        # A typed red line is caught even when nothing in the wording says so.
        quiet_delete = ToolRequest("files.delete", {"path": str(self.workspace / "a")}, "整理一下")
        self.assertEqual(verdict(quiet_delete)["sensitive_action"], "deletion")

        ordinary = ToolRequest("computer.click", {"x": 5, "y": 5}, "点击候选目标：下一页")
        self.assertTrue(verdict(ordinary)["allowed"])
        self.assertFalse(verdict(ordinary)["requires_approval"])

    def test_ok_ww_runner_has_no_baked_in_path(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OK_WW_RUNNER", None)
            self.assertIsNone(ok_ww_runner_path())
            self.assertTrue(ok_ww_setup_hint())

            os.environ["OK_WW_RUNNER"] = str(self.workspace / "absent.ps1")
            self.assertIsNone(ok_ww_runner_path())

            runner = self.workspace / "run_ok_ww.ps1"
            runner.write_text("# fixture\n", encoding="utf-8")
            os.environ["OK_WW_RUNNER"] = str(runner)
            self.assertEqual(ok_ww_runner_path(), runner)

    def test_scene_session_is_quiet_by_default_and_event_driven(self) -> None:
        scene = SceneSession()
        scene.reset(mode="quiet")
        quiet = scene.observe("A room", ["但是这不可能！"], now=100, proactive_enabled=True)
        self.assertFalse(quiet.should_comment)
        self.assertFalse(quiet.raw_media_retained)
        scene.reset(mode="commentary", min_comment_interval_seconds=8)
        event = scene.observe("A room", ["但是这不可能！"], now=100, proactive_enabled=True)
        self.assertTrue(event.should_comment)
        unchanged = scene.observe("A room", ["但是这不可能！"], now=120, proactive_enabled=True)
        self.assertFalse(unchanged.should_comment)

    def test_game_adapter_lifecycle_and_minecraft_modes(self) -> None:
        registry = GameAdapterRegistry(self.workspace, self.store.data_home)
        review = registry.install("minecraft")
        self.assertTrue(review["requires_approval"])
        installed = registry.install("minecraft", confirmed=True)
        self.assertTrue(installed["adapter"]["installed"])
        dry_run = registry.prepare("minecraft", "companion", "跟随我", True)
        self.assertTrue(dry_run["ok"])
        self.assertIn("ready", dry_run)
        unsupported = registry.prepare("minecraft", "spectator", "test", True)
        self.assertEqual(unsupported["error"], "unsupported_game_mode")
        self.assertTrue(registry.uninstall("minecraft", confirmed=True)["ok"])
        self.assertFalse(registry.status("minecraft")["adapter"]["installed"])


if __name__ == "__main__":
    unittest.main()
