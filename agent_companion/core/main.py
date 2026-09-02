from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.server import JsonRpcBridge


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Joi core once.")
    parser.add_argument("text", nargs="*", help="User request")
    parser.add_argument("--workspace", default=".", help="Workspace root")
    parser.add_argument("--serve", action="store_true", help="Run the WebSocket JSON-RPC bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--session-token", default=os.environ.get("JOI_CORE_SESSION_TOKEN", ""))
    parser.add_argument("--instance-id", default=os.environ.get("JOI_CORE_INSTANCE_ID", ""))
    parser.add_argument("--ready-file", default=os.environ.get("JOI_CORE_READY_FILE", ""))
    parser.add_argument("--parent-pid", type=int, default=0)
    args = parser.parse_args()
    if args.serve:
        import asyncio

        asyncio.run(
            JsonRpcBridge(
                Path(args.workspace),
                args.host,
                args.port,
                session_token=args.session_token,
                instance_id=args.instance_id,
                ready_file=Path(args.ready_file) if args.ready_file else None,
                parent_pid=args.parent_pid,
            ).serve()
        )
        return 0
    if not args.text:
        parser.print_help()
        return 2
    app = AgentCompanionApp(Path(args.workspace))
    events = app.handle_user_text(" ".join(args.text))
    print(json.dumps([event.to_dict() for event in events], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
