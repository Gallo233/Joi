from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agent_companion.core.capability_orchestrator import ComputerUseOrchestrator
from agent_companion.core.collaboration_store import CollaborationStore
from agent_companion.core.game_adapters import GameAdapterRegistry
from agent_companion.core.scene_session import SceneSession
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, ToolRequest, VoiceLine
from agent_companion.core.server import _is_always_sensitive_request, _request_within_bound_scope


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
        self.assertTrue(_is_always_sensitive_request(ToolRequest("files.delete", {"path": str(self.workspace / "a")})))
        self.assertTrue(_is_always_sensitive_request(ToolRequest("browser.click", {"intent": "付款"})))

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
