from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent_companion.core.joi_mcp_server import JoiMcpServer, TOOL_SCHEMAS


class FakeJoiMcpServer(JoiMcpServer):
    def __init__(self, workspace: Path) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        super().__init__("ws://127.0.0.1:8765", workspace)

    async def _core(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, params))
        return {"ok": True, "method": method, "params": params}


class McpToolRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_covers_every_published_tool_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = FakeJoiMcpServer(Path(directory))

            published = {str(schema["name"]) for schema in TOOL_SCHEMAS}

            self.assertEqual(set(server._tool_router.methods()), published)

    async def test_simple_tools_delegate_to_core_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = FakeJoiMcpServer(Path(directory))

            result = await server._call_tool("joi_memory_recall", {"query": "偏好", "limit": "5"})

            self.assertTrue(result["ok"])
            self.assertEqual(server.calls[-1], ("memory.recall", {"query": "偏好", "limit": 5}))

    async def test_unknown_tool_keeps_public_error_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = FakeJoiMcpServer(Path(directory))

            with self.assertRaisesRegex(ValueError, "unknown Joi MCP tool"):
                await server._call_tool("missing", {})


if __name__ == "__main__":
    unittest.main()
