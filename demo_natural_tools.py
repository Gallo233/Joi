from __future__ import annotations

import time
from pathlib import Path

from mvp.agent_runtime import AgentRuntime


def main() -> None:
    runtime = AgentRuntime(Path.cwd())
    prompts = [
        "有哪些工具可以用？",
        "搜索 AgentRuntime",
        "看一下 README.md",
        "运行命令 dir",
        "列出 MCP 配置",
        "查看事件源",
        "打开 https://example.com",
        "给当前网页截图",
    ]
    for prompt in prompts:
        task_id = runtime.run_natural_language_request(prompt)
        print(f"user: {prompt}")
        print(f"task: {task_id or 'no tool'}")

    deadline = time.time() + 10
    while time.time() < deadline:
        for event in runtime.drain_events():
            detail = event.metadata.get("detail", "")
            print(f"{event.type.value}: {event.title} -> {event.message}")
            if detail:
                print(detail[:500])
        time.sleep(0.2)


if __name__ == "__main__":
    main()
