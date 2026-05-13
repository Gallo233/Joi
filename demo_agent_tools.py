from __future__ import annotations

import time
from pathlib import Path

from mvp.agent_runtime import AgentRuntime


def main() -> None:
    runtime = AgentRuntime(Path.cwd())
    runtime.run_tool_request("search", "AgentRuntime")
    runtime.run_tool_request("read", "mvp/agent_runtime.py")
    runtime.run_tool_request("cmd", "dir")
    runtime.run_tool_request("cmd", "pip install requests")

    deadline = time.time() + 8
    while time.time() < deadline:
        for event in runtime.drain_events():
            detail = event.metadata.get("detail", "")
            print(f"{event.type.value}: {event.title} -> {event.message}")
            if detail:
                print(detail[:500])
        time.sleep(0.2)


if __name__ == "__main__":
    main()
