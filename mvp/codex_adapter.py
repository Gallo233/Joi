from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodexTaskRequest:
    id: str
    goal: str
    cwd: str
    mode: str = "execute"
    approval_policy: str = "never"
    sandbox: str = "workspace-write"
    model: str = ""
    auto_start: bool = False
    status: str = "queued"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "cwd": self.cwd,
            "mode": self.mode,
            "approval_policy": self.approval_policy,
            "sandbox": self.sandbox,
            "model": self.model,
            "auto_start": self.auto_start,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "CodexTaskRequest":
        return cls(
            id=str(row.get("id") or ""),
            goal=str(row.get("goal") or ""),
            cwd=str(row.get("cwd") or ""),
            mode=str(row.get("mode") or "execute"),
            approval_policy=str(row.get("approval_policy") or "never"),
            sandbox=str(row.get("sandbox") or "workspace-write"),
            model=str(row.get("model") or ""),
            auto_start=bool(row.get("auto_start", False)),
            status=str(row.get("status") or "queued"),
            created_at=float(row.get("created_at") or time.time()),
        )


class CodexAdapter:
    """File-backed bridge to a Codex/Claude-Code-style executor."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.event_dir = self.workspace / "data" / "agent_events"
        self.request_path = self.event_dir / "codex_requests.jsonl"
        self.event_path = self.event_dir / "codex.jsonl"
        self.cancel_path = self.event_dir / "codex_cancellations.jsonl"
        self.run_dir = self.workspace / "data" / "codex_runs"

    def submit_task(
        self,
        goal: str,
        cwd: str | None = None,
        mode: str = "execute",
        approval_policy: str = "never",
        sandbox: str = "workspace-write",
        model: str = "",
        auto_start: bool = False,
    ) -> CodexTaskRequest:
        cleaned_goal = " ".join((goal or "").strip().split())
        if not cleaned_goal:
            raise ValueError("Codex 任务目标不能为空。")
        task = CodexTaskRequest(
            id=f"codex-{uuid.uuid4().hex[:10]}",
            goal=cleaned_goal,
            cwd=str(self._resolve_cwd(cwd)),
            mode=mode or "execute",
            approval_policy=approval_policy or "never",
            sandbox=sandbox or "workspace-write",
            model=model or "",
            auto_start=bool(auto_start),
        )
        self._append_jsonl(self.request_path, task.to_dict())
        if auto_start:
            self.start_task(task)
        return task

    def start_task(self, task: CodexTaskRequest) -> None:
        script = self.workspace / "run_codex_task.py"
        if not script.is_file():
            raise RuntimeError(f"Codex 执行器脚本不存在: {script}")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.run_dir / f"{task.id}.launcher.log"
        command = [
            sys.executable,
            str(script),
            "--workspace",
            str(self.workspace),
            "--task-id",
            task.id,
            "--cwd",
            task.cwd,
            "--goal",
            task.goal,
            "--approval-policy",
            task.approval_policy,
            "--sandbox",
            task.sandbox,
        ]
        if task.model:
            command.extend(["--model", task.model])
        log = log_path.open("a", encoding="utf-8")
        try:
            subprocess.Popen(
                command,
                cwd=str(self.workspace),
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        finally:
            log.close()

    def start_task_by_id(self, task_id: str = "", latest: bool = False, allow_restart: bool = False) -> CodexTaskRequest:
        task = self.find_task(task_id=task_id, latest=latest)
        if task is None:
            raise ValueError("没有找到可启动的 Codex 任务。")
        latest_event = self.latest_event(task.id)
        if latest_event and not allow_restart:
            event_type = str(latest_event.get("type") or "").casefold()
            if event_type in {"start", "started", "task_started", "progress", "task_progress"}:
                raise ValueError(f"Codex 任务已经在执行中：{task.id}")
            if event_type in {"complete", "completed", "task_completed"}:
                raise ValueError(f"Codex 任务已经完成：{task.id}")
        self.start_task(task)
        return task

    def find_task(self, task_id: str = "", latest: bool = False) -> CodexTaskRequest | None:
        requests = self._read_jsonl(self.request_path)
        if not requests:
            return None
        if task_id:
            target = task_id.strip()
            for row in reversed(requests):
                if row.get("id") == target:
                    return CodexTaskRequest.from_dict(row)
            return None
        if latest:
            return CodexTaskRequest.from_dict(requests[-1])
        return None

    def latest_event(self, task_id: str) -> dict[str, Any] | None:
        latest: dict[str, Any] | None = None
        for row in self._read_jsonl(self.event_path):
            if row.get("task_id") == task_id:
                latest = row
        return latest

    def cancel_task(self, task_id: str, reason: str = "") -> dict[str, Any]:
        cleaned = (task_id or "").strip()
        if not cleaned:
            raise ValueError("task_id 不能为空。")
        payload = {
            "task_id": cleaned,
            "reason": reason.strip() or "user_cancelled",
            "created_at": time.time(),
        }
        self._append_jsonl(self.cancel_path, payload)
        return payload

    def status(self, task_id: str = "", limit: int = 10) -> dict[str, Any]:
        requests = self._read_jsonl(self.request_path)
        events = self._read_jsonl(self.event_path)
        cancellations = self._read_jsonl(self.cancel_path)
        if task_id:
            target = task_id.strip()
            requests = [row for row in requests if row.get("id") == target]
            events = [row for row in events if row.get("task_id") == target]
            cancellations = [row for row in cancellations if row.get("task_id") == target]
        else:
            requests = requests[-limit:]
            known_ids = {str(row.get("id") or "") for row in requests}
            events = [row for row in events if str(row.get("task_id") or "") in known_ids]
            cancellations = [row for row in cancellations if str(row.get("task_id") or "") in known_ids]
        latest_by_task: dict[str, dict[str, Any]] = {}
        for event in events:
            tid = str(event.get("task_id") or "")
            if tid:
                latest_by_task[tid] = event
        return {
            "requests": requests,
            "latest_events": latest_by_task,
            "cancellations": cancellations,
            "paths": {
                "requests": self.request_path.relative_to(self.workspace).as_posix(),
                "events": self.event_path.relative_to(self.workspace).as_posix(),
                "cancellations": self.cancel_path.relative_to(self.workspace).as_posix(),
            },
        }

    def _resolve_cwd(self, cwd: str | None) -> Path:
        raw = Path((cwd or "").strip()) if cwd else self.workspace
        candidate = raw if raw.is_absolute() else self.workspace / raw
        resolved = candidate.resolve()
        if not resolved.exists():
            raise ValueError(f"工作目录不存在：{resolved}")
        return resolved

    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows
