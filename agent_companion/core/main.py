from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_companion.core.app import AgentCompanionApp


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Agent Companion core once.")
    parser.add_argument("text", nargs="+", help="User request")
    parser.add_argument("--workspace", default=".", help="Workspace root")
    parser.add_argument("--approve", action="store_true", help="Approve medium/high risk actions for this run")
    args = parser.parse_args()
    app = AgentCompanionApp(Path(args.workspace))
    events = app.handle_user_text(" ".join(args.text), approved=args.approve)
    print(json.dumps([event.to_dict() for event in events], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

