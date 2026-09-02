from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from agent_companion.core.capability_orchestrator import ComputerUseOrchestrator
from agent_companion.core.collaboration_store import CollaborationStore
from agent_companion.core.computer_use.cua_driver import CuaDriverBackend, _decode_json
from agent_companion.core.computer_use.schemas import ComputerAction


class CuaDriverBackendTests(unittest.TestCase):
    def test_prefixed_cli_result_is_decoded(self) -> None:
        payload = _decode_json('driver output\n{"structuredContent":{"windows":[{"pid":7}]}}')
        self.assertEqual(payload["structuredContent"]["windows"][0]["pid"], 7)

    @patch("agent_companion.core.computer_use.cua_driver.shutil.which", return_value="/tmp/cua-driver")
    def test_background_action_uses_bound_window_coordinates(self, _which) -> None:
        with tempfile.TemporaryDirectory() as temp:
            backend = CuaDriverBackend(Path(temp), session_id="session-1")
            calls: list[tuple[str, dict]] = []

            def fake_call(tool: str, arguments: dict, timeout: int = 20):
                calls.append((tool, arguments))
                if tool == "launch_app":
                    return {"structuredContent": {"pid": 91, "windows": [{"pid": 91, "window_id": 8, "app_name": "Notes", "title": "Notes"}]}}
                return {"content": [{"type": "text", "text": "ok"}]}

            backend._call = fake_call  # type: ignore[method-assign]
            self.assertTrue(backend.perform(ComputerAction("open_app", app_name="Notes")).ok)
            self.assertTrue(backend.perform(ComputerAction("click", x=120, y=80)).ok)
            tool, arguments = calls[-1]
            self.assertEqual(tool, "click")
            self.assertEqual((arguments["pid"], arguments["window_id"]), (91, 8))
            self.assertEqual(arguments["delivery_mode"], "background")

    @patch("agent_companion.core.computer_use.cua_driver.shutil.which", return_value="/tmp/cua-driver")
    def test_observation_keeps_window_local_screenshot_space(self, _which) -> None:
        with tempfile.TemporaryDirectory() as temp:
            backend = CuaDriverBackend(Path(temp), session_id="session-2")

            def fake_call(tool: str, arguments: dict, timeout: int = 20):
                if tool == "list_windows":
                    return {"structuredContent": {"windows": [{"pid": 42, "window_id": 13, "app_name": "Finder", "title": "Downloads", "z_index": 9}]}}
                if tool == "get_window_state":
                    path = Path(arguments["screenshot_out_file"])
                    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 1440, 900))
                    return {"structuredContent": {"tree_markdown": "[element_index 1] Downloads", "elements": [{"element_index": 1}]}}
                raise AssertionError(tool)

            backend._call = fake_call  # type: ignore[method-assign]
            observed = backend.observe(query="Downloads")
            self.assertEqual((observed.width, observed.height), (1440, 900))
            self.assertEqual(observed.window_handle, 13)
            self.assertEqual(observed.source, "cua_driver_window")
            self.assertEqual(observed.ocr["elements"][0]["element_index"], 1)

    @patch("agent_companion.core.capability_orchestrator._cua_available", return_value=False)
    def test_orchestrator_never_claims_missing_cua(self, _available) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = CollaborationStore(root, default_character_id="character")
            inventory = ComputerUseOrchestrator(root, store).driver_inventory("cua")
            self.assertEqual(inventory.selected, "native")
            self.assertFalse(inventory.cua_available)
            self.assertIn("降级", inventory.notes[0])


if __name__ == "__main__":
    unittest.main()
