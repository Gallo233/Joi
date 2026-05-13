from __future__ import annotations

import time

from mvp.agent_runtime import AgentRuntime


def main() -> None:
    runtime = AgentRuntime()
    runtime.start_demo_task("整理 README 并生成完成播报")
    runtime.start_demo_task("安装依赖，需要提权确认")

    deadline = time.time() + 5
    while time.time() < deadline:
        for event in runtime.drain_events():
            print(event.type.value, event.title, event.message)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
