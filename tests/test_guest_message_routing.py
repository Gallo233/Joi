"""A guest Core answers in conversation, never through a local capability.

`user.message` is the one RPC a visitor must be allowed to call, so the method
allowlist cannot protect anything that routes on the *text* of that message.
`submit_user_text` had five such routes -- Minecraft, Computer Use, the Codex
runtime, the agent CLI takeover and the watch loop -- and
`CodexRuntimeSession.should_handle` is not a "does this look like code"
heuristic at all: it reports whether Codex is the selected runtime, which is the
default. An ungated guest Core therefore sent every visitor sentence into the
local coding agent instead of into conversation, and would have run it wherever
a `codex` binary happened to be on PATH.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agent_companion.core.server import JsonRpcBridge


GUEST_METHODS = ("user.message", "core.ping")


def _bridge(workspace: Path, *, guest: bool) -> JsonRpcBridge:
    return JsonRpcBridge(
        workspace,
        "127.0.0.1",
        0,
        allowed_methods=GUEST_METHODS if guest else (),
    )


class GuestMessageRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_guest_text_never_reaches_a_local_capability(self) -> None:
        bridge = _bridge(self.workspace, guest=True)
        self.assertTrue(bridge.guest_mode)
        # Text chosen to trip every router: a coding request that also names a
        # game and a desktop verb.
        text = "帮我在 Minecraft 里打开箱子，然后写代码修一下这个 bug"
        with mock.patch.object(bridge.codex_runtime, "run_user_text") as codex, \
             mock.patch.object(bridge, "minecraft_text_goal_command") as minecraft, \
             mock.patch.object(bridge.collaboration, "start_session") as computer_use, \
             mock.patch.object(bridge.app, "handle_user_text", return_value=[]) as chat:
            result = bridge.submit_user_text(text)
        codex.assert_not_called()
        minecraft.assert_not_called()
        computer_use.assert_not_called()
        chat.assert_called_once_with(text)
        self.assertTrue(result["ok"])
        self.assertTrue(result["submitted"])

    def test_desktop_keeps_its_existing_routing(self) -> None:
        bridge = _bridge(self.workspace, guest=False)
        self.assertFalse(bridge.guest_mode)
        with mock.patch.object(bridge.codex_runtime, "should_handle", return_value=True), \
             mock.patch.object(bridge.app, "should_handle_locally_before_agent_cli", return_value=False), \
             mock.patch.object(bridge.codex_runtime, "run_user_text", return_value={"ok": True}) as codex:
            bridge.submit_user_text("写代码修一下这个 bug")
        codex.assert_called_once()


if __name__ == "__main__":
    unittest.main()
