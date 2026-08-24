from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_companion.core.server import JsonRpcBridge
from agent_companion.web.broker import PUBLIC_RPC_METHODS, _http_base, _origins, _route_id, build_config


class WebBrokerContractTests(unittest.TestCase):
    def test_port_step_is_never_less_than_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "seed"
            seed.mkdir()
            (seed / "config.yaml").write_text("llm: {}\n", encoding="utf-8")
            args = argparse.Namespace(
                host="127.0.0.1", port=9080, public_base="https://joi.example.com",
                allowed_origins="https://www.example.com", seed_workspace=str(seed),
                guest_config_template="", sessions_root=str(root / "sessions"), state_root=str(root / "state"),
                core_python="python", port_start=9100, port_end=9110, port_step=1,
                max_concurrent=2, ttl_seconds=60, ready_timeout_seconds=1.0, ip_daily_sessions=5,
                session_token_limit=100, daily_token_limit=1000, realtime_session_seconds=30,
                realtime_total_seconds=60, daily_realtime_seconds=600,
            )
            with patch.dict(os.environ, {"JOI_WEB_IP_HASH_SECRET": "x" * 32}):
                config = build_config(args)
            self.assertEqual(config.port_step, 2)

    def test_only_exact_http_origins_are_accepted(self) -> None:
        self.assertEqual(_origins("https://example.com, http://localhost:3000"), ("https://example.com", "http://localhost:3000"))
        with self.assertRaises(ValueError):
            _origins("https://example.com/path")
        with self.assertRaises(ValueError):
            _http_base("https://joi.example.com/nested")

    def test_public_rpc_surface_omits_local_mutation_and_machine_methods(self) -> None:
        allowed = set(PUBLIC_RPC_METHODS)
        for forbidden in (
            "skill.install", "runtime.config.apply", "byok.connect",
            "character.import", "artifact.read", "voice.realtime.game.control",
        ):
            self.assertNotIn(forbidden, allowed)
        self.assertIn("user.message", allowed)
        self.assertIn("character.activate", allowed)

    def test_proxy_routes_accept_only_bounded_session_ids(self) -> None:
        session_id = "a" * 24
        self.assertEqual(_route_id(f"/ws/{session_id}", "/ws/"), session_id)
        self.assertEqual(_route_id("/ws/../../etc/passwd", "/ws/"), "")

    def test_guest_internal_errors_do_not_expose_exception_text(self) -> None:
        class Socket:
            def __init__(self) -> None:
                self.sent: list[str] = []

            async def send(self, value: str) -> None:
                self.sent.append(value)

        class BrokenRouter:
            async def dispatch(self, _method: str, _params: dict[str, object]) -> None:
                raise RuntimeError("private path and provider detail")

        bridge = object.__new__(JsonRpcBridge)
        bridge.allowed_methods = frozenset({"core.ping"})
        bridge.guest_mode = True
        bridge.rpc = BrokenRouter()
        socket = Socket()
        bridge._client_owners = {socket: "guest"}
        asyncio.run(bridge._handle_message(socket, json.dumps({
            "jsonrpc": "2.0", "id": 7, "method": "core.ping", "params": {},
        })))
        payload = json.loads(socket.sent[0])
        self.assertEqual(payload["error"]["message"], "internal error")


if __name__ == "__main__":
    unittest.main()
