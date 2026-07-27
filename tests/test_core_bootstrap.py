from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_companion.core.server import JsonRpcBridge, _websocket_session_token
from agent_companion.core.sidecar_entry import seed_installed_workspace


class CoreBootstrapTests(unittest.TestCase):
    def test_seed_installed_workspace_copies_builtins_without_overwriting_user_data(self) -> None:
        with tempfile.TemporaryDirectory() as source_directory, tempfile.TemporaryDirectory() as target_directory:
            source = Path(source_directory)
            target = Path(target_directory)
            (source / "config.example.yaml").write_text("source: true\n", encoding="utf-8")
            skill = source / "agent_companion" / "skills" / "sample" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("source skill\n", encoding="utf-8")
            destination_config = target / "config.example.yaml"
            destination_config.write_text("user: true\n", encoding="utf-8")

            seed_installed_workspace(target, source)

            self.assertEqual(destination_config.read_text(encoding="utf-8"), "user: true\n")
            self.assertEqual(
                (target / "agent_companion" / "skills" / "sample" / "SKILL.md").read_text(encoding="utf-8"),
                "source skill\n",
            )

    def test_websocket_token_supports_current_and_legacy_websockets_shapes(self) -> None:
        current = SimpleNamespace(request=SimpleNamespace(path="/?token=current-token"))
        legacy = SimpleNamespace(path="/rpc?token=legacy-token")

        self.assertEqual(_websocket_session_token(current), "current-token")
        self.assertEqual(_websocket_session_token(legacy), "legacy-token")

    def test_websocket_rejects_missing_session_token_before_registering_client(self) -> None:
        class FakeWebSocket:
            request = SimpleNamespace(path="/")

            def __init__(self) -> None:
                self.closed: tuple[int, str] | None = None

            async def close(self, *, code: int, reason: str) -> None:
                self.closed = (code, reason)

        bridge = object.__new__(JsonRpcBridge)
        bridge.session_token = "launch-secret"
        bridge.clients = set()
        websocket = FakeWebSocket()

        asyncio.run(JsonRpcBridge._client_handler(bridge, websocket))

        self.assertEqual(websocket.closed, (4401, "joi_core_auth_required"))
        self.assertEqual(bridge.clients, set())


if __name__ == "__main__":
    unittest.main()
