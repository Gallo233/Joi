#!/usr/bin/env python3
"""Offline P5 smoke for the reviewed built Minecraft bridge.

No Minecraft client or network is opened. The bridge's deterministic fake
world exercises all ten primitives plus the no-replay recovery path.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_companion.core.minecraft_bridge import MinecraftBridgeClient
from agent_companion.core.minecraft_contract import canonicalize_minecraft_scope


SCOPE = canonicalize_minecraft_scope(
    {
        "server_id": "local-hmcl",
        "world": "world",
        "dimensions": ["overworld"],
        "max_radius": 32,
        "max_actions": 30,
        "max_blocks_changed": 64,
        "allowed_blocks": ["oak_log", "oak_planks", "cobblestone", "crafting_table", "chest"],
        "allowed_players": ["Player"],
        "allow_build": True,
        "allow_containers": True,
    }
)

INTENTS = (
    {"action": "observe", "dimension": "overworld", "radius": 8},
    {"action": "inventory"},
    {"action": "follow_player", "player": "Player", "distance": 3, "duration_seconds": 2},
    {"action": "come_to_player", "player": "Player", "distance": 2},
    {"action": "collect", "block": "oak_log", "count": 2, "radius": 8, "dimension": "overworld"},
    {"action": "mine", "block": "cobblestone", "count": 1, "radius": 8, "dimension": "overworld"},
    {"action": "craft", "item": "oak_planks", "count": 1},
    {"action": "eat", "item": "bread"},
    {"action": "place_blueprint", "anchor": "bot", "dimension": "overworld", "blocks": [{"offset": [1, 0, 0], "block": "oak_planks"}]},
    {"action": "deposit", "container": "chest", "items": [{"item": "oak_log", "count": 1}], "radius": 8},
)


def built_command(workspace: Path) -> list[str]:
    node = shutil.which("node")
    bridge = workspace / "agent_companion" / "adapters" / "minecraft-bridge" / "dist" / "index.js"
    if not node or not bridge.is_file():
        raise RuntimeError("minecraft_built_bridge_missing")
    return [node, str(bridge)]


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]
    vendor = workspace / "agent_companion" / "adapters" / "minecraft-bridge" / "dist" / "vendor"
    environment = {
        "JOI_MINECRAFT_FAKE": "1",
        "JOI_MINECRAFT_FAKE_DELAY_MS": "2",
        "JOI_MINECRAFT_SERVER_ID": "local-hmcl",
        "JOI_MINECRAFT_WORLD": "world",
        "JOI_MINECRAFT_VENDOR_DIR": str(vendor),
    }
    client = MinecraftBridgeClient(
        built_command(workspace),
        session_id="session-p5-smoke",
        mode="companion",
        scope=SCOPE,
        budget={"max_steps": 30, "max_seconds": 60},
        environment=environment,
        response_timeout=10,
    )
    try:
        started = client.start()
        if not started.get("ok"):
            raise RuntimeError("minecraft_fake_start_failed")
        for index, intent in enumerate(INTENTS, start=1):
            result = client.submit_goal(f"goal-smoke-{index}", intent)
            if not result.get("ok") or not result.get("verified"):
                raise RuntimeError(f"minecraft_fake_primitive_failed:{intent['action']}")
    finally:
        client.close()

    recovery = MinecraftBridgeClient(
        built_command(workspace),
        session_id="session-p5-recovery",
        mode="companion",
        scope=SCOPE,
        budget={"max_steps": 4, "max_seconds": 30},
        environment={**environment, "JOI_MINECRAFT_FAKE_DISCONNECT_AFTER_EFFECT": "1"},
        response_timeout=5,
    )
    try:
        if not recovery.start().get("ok"):
            raise RuntimeError("minecraft_fake_recovery_start_failed")
        result = recovery.submit_goal(
            "goal-smoke-recovery",
            {"action": "mine", "block": "oak_log", "count": 2, "radius": 8, "dimension": "overworld"},
        )
        deadline = time.monotonic() + 1
        while recovery.alive and time.monotonic() < deadline:
            time.sleep(0.01)
        if result.get("ok") or not result.get("recovery_required") or recovery.alive:
            raise RuntimeError("minecraft_fake_recovery_gate_failed")
    finally:
        recovery.close()
    print("minecraft_p5_smoke_ok primitives=10 recovery=no_replay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
