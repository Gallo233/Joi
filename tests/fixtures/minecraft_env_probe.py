from __future__ import annotations

import json
import os
import sys


if os.environ.get("JOI_SECRET_CANARY") or os.environ.get("JOI_MINECRAFT_PASSWORD"):
    raise SystemExit(9)

print(
    json.dumps(
        {
            "protocol": "joi.game_adapter",
            "version": 2,
            "type": "bridge.ready",
            "session_id": "",
            "message_id": "ready",
            "sequence": 1,
            "bridge_instance_id": "probe",
            "reply_to": "",
            "payload": {"capabilities": [], "fake": True},
        }
    ),
    flush=True,
)
sys.stdin.readline()
