from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

from mvp.agent_event_bridge import append_external_event


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Codex task and write events back to Shinsekai.")
    parser.add_argument("--workspace", default=".", help="Shinsekai workspace")
    parser.add_argument("--task-id", required=True, help="Codex task id")
    parser.add_argument("--cwd", required=True, help="Task working directory")
    parser.add_argument("--goal", required=True, help="Task goal")
    parser.add_argument("--approval-policy", default="never", help="Codex approval policy")
    parser.add_argument("--sandbox", default="workspace-write", help="Codex sandbox mode")
    parser.add_argument("--model", default="", help="Optional Codex model")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    event_path = workspace / "data" / "agent_events" / "codex.jsonl"
    run_dir = workspace / "data" / "codex_runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir / f"{args.task_id}.jsonl"
    stderr_path = run_dir / f"{args.task_id}.stderr.log"
    final_path = run_dir / f"{args.task_id}.final.txt"

    append_external_event(
        event_path,
        {
            "type": "start",
            "task_id": args.task_id,
            "title": "Codex 任务",
            "message": "Codex 执行器已启动。",
            "metadata": {"tool": "codex.executor", "detail": args.goal},
        },
    )

    codex = shutil.which("codex")
    if not codex:
        append_external_event(
            event_path,
            {
                "type": "failed",
                "task_id": args.task_id,
                "title": "Codex 任务",
                "message": "没有找到 codex CLI。",
                "metadata": {"tool": "codex.executor"},
            },
        )
        return 1

    command = [
        codex,
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--cd",
        str(Path(args.cwd).resolve()),
        "--sandbox",
        args.sandbox,
        "--ask-for-approval",
        args.approval_policy,
        "--output-last-message",
        str(final_path),
    ]
    if args.model:
        command.extend(["--model", args.model])
    command.append(args.goal)

    append_external_event(
        event_path,
        {
            "type": "progress",
            "task_id": args.task_id,
            "title": "Codex 任务",
            "message": "Codex CLI 正在执行任务。",
            "metadata": {
                "tool": "codex.executor",
                "detail": " ".join(command[:7]) + " ...",
            },
        },
    )

    started = time.time()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        result = subprocess.run(command, cwd=args.cwd, stdout=stdout, stderr=stderr, text=True)

    duration = time.time() - started
    final_text = final_path.read_text(encoding="utf-8").strip() if final_path.is_file() else ""
    stderr_text = stderr_path.read_text(encoding="utf-8").strip() if stderr_path.is_file() else ""
    detail = "\n".join(
        part
        for part in (
            f"goal: {args.goal}",
            f"cwd: {args.cwd}",
            f"duration_seconds: {duration:.1f}",
            f"stdout_jsonl: {stdout_path.relative_to(workspace).as_posix()}",
            f"stderr_log: {stderr_path.relative_to(workspace).as_posix()}",
            f"final_message:\n{final_text}" if final_text else "",
            f"stderr_tail:\n{stderr_text[-2000:]}" if stderr_text else "",
        )
        if part
    )

    append_external_event(
        event_path,
        {
            "type": "complete" if result.returncode == 0 else "failed",
            "task_id": args.task_id,
            "title": "Codex 任务",
            "message": "Codex 任务已完成。" if result.returncode == 0 else f"Codex 任务失败，退出码 {result.returncode}。",
            "detail": detail,
            "metadata": {
                "tool": "codex.executor",
                "returncode": str(result.returncode),
                "final_path": final_path.relative_to(workspace).as_posix(),
            },
        },
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
