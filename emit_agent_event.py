from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from mvp.agent_event_bridge import append_external_event


def main() -> None:
    parser = argparse.ArgumentParser(description="Append an external Agent event for the desktop avatar.")
    parser.add_argument("type", help="start, progress, complete, failed, or approval")
    parser.add_argument("title", help="Task title")
    parser.add_argument("message", nargs="?", default="", help="Short message to speak/show")
    parser.add_argument("--detail", default="", help="Detailed result or approval reason")
    parser.add_argument("--tool", default="external", help="Event source name")
    parser.add_argument("--task-id", default="", help="Stable task id for follow-up events")
    parser.add_argument(
        "--source",
        choices=("inbox", "codex", "mcp", "plugins", "browser"),
        default="inbox",
        help="Named event source inbox",
    )
    parser.add_argument("--inbox", default="data/agent_events/inbox.jsonl", help="Inbox JSONL path")
    args = parser.parse_args()

    title = args.title.strip()
    inbox = Path(args.inbox)
    if args.source != "inbox" and args.inbox == "data/agent_events/inbox.jsonl":
        inbox = Path("data") / "agent_events" / f"{args.source}.jsonl"
    event = {
        "type": args.type,
        "task_id": args.task_id.strip() or f"external-{uuid.uuid4().hex[:8]}",
        "title": title,
        "message": args.message.strip() or title,
        "detail": args.detail.strip(),
        "metadata": {"tool": args.tool.strip() or args.source},
    }
    append_external_event(inbox, event)
    print(f"wrote {args.type}: {title} -> {inbox}")


if __name__ == "__main__":
    main()
