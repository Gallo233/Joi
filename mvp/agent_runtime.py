from __future__ import annotations

import queue
import re
import threading
import time
import uuid
import json
from dataclasses import dataclass
from pathlib import Path

from mvp.agent_event_bridge import AgentEventBridge
from mvp.agent_runtime_types import AgentEvent, AgentEventType, AgentTask, ApprovalRequest
from mvp.agent_tool_registry import ToolCall, ToolResult
from mvp.agent_tools import AgentToolbox


@dataclass(frozen=True)
class AgentPlan:
    user_request: str
    steps: list[ToolCall]


class AgentRuntime:
    """Minimal event-oriented runtime with a conservative local tool layer."""

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = (workspace or Path.cwd()).resolve()
        self._events: queue.Queue[AgentEvent] = queue.Queue()
        self._tasks: dict[str, AgentTask] = {}
        self._pending_approvals: dict[str, ToolCall] = {}
        self._tools = AgentToolbox(self._workspace)
        self._bridges = [
            AgentEventBridge(self._workspace / "data" / "agent_events" / f"{name}.jsonl", name)
            for name in ("inbox", "codex", "mcp", "plugins", "browser")
        ]

    def start_demo_task(self, user_request: str) -> str:
        title = self._task_title(user_request)
        task = AgentTask(
            id=uuid.uuid4().hex[:12],
            title=title,
            user_request=user_request.strip(),
        )
        self._tasks[task.id] = task
        self._emit(
            AgentEvent(
                type=AgentEventType.TASK_STARTED,
                task_id=task.id,
                title=task.title,
                message=f"已开始：{task.title}。",
            )
        )
        worker = threading.Thread(target=self._run_demo_task, args=(task.id,), daemon=True)
        worker.start()
        return task.id

    def emit_completed(self, title: str, message: str) -> str:
        task_id = uuid.uuid4().hex[:12]
        self._tasks[task_id] = AgentTask(
            id=task_id,
            title=title.strip() or "任务",
            user_request=title.strip(),
            status=AgentEventType.TASK_COMPLETED,
        )
        self._emit(
            AgentEvent(
                type=AgentEventType.TASK_COMPLETED,
                task_id=task_id,
                title=title.strip() or "任务",
                message=message.strip() or f"{title.strip() or '任务'} 已完成。",
            )
        )
        return task_id

    def emit_approval_required(self, title: str, detail: str) -> str:
        task_id = uuid.uuid4().hex[:12]
        approval = ApprovalRequest(title=title.strip() or "需要授权", detail=detail.strip())
        self._tasks[task_id] = AgentTask(
            id=task_id,
            title=approval.title,
            user_request=detail.strip(),
            status=AgentEventType.APPROVAL_REQUIRED,
        )
        self._emit(
            AgentEvent(
                type=AgentEventType.APPROVAL_REQUIRED,
                task_id=task_id,
                title=approval.title,
                message=approval.detail or "这一步需要你确认授权。",
                approval=approval,
            )
        )
        return task_id

    def run_tool_request(self, tool_name: str, argument: str | dict = "") -> str:
        return self.run_tool_call(self._tools.build_call(tool_name, argument))

    def run_natural_language_request(self, user_text: str) -> str | None:
        call = self._tools.plan_natural_language(user_text)
        if call is None:
            return None
        return self.run_tool_call(call)

    def run_agent_request(self, user_text: str, force: bool = False) -> str | None:
        plan = self._build_agent_plan(user_text, force=force)
        if plan is None:
            return None
        title = self._task_title(f"Agent: {user_text}")
        task = AgentTask(
            id=uuid.uuid4().hex[:12],
            title=title,
            user_request=user_text.strip(),
        )
        self._tasks[task.id] = task
        self._emit(
            AgentEvent(
                type=AgentEventType.TASK_STARTED,
                task_id=task.id,
                title=task.title,
                message=f"开始执行 Agent 任务：{task.title}。",
                metadata=self._agent_metadata(plan),
            )
        )
        worker = threading.Thread(target=self._run_agent_plan, args=(task.id, plan), daemon=True)
        worker.start()
        return task.id

    def run_tool_call(self, call: ToolCall) -> str:
        definition = self._tools.get_definition(call.tool_name)
        title = self._tool_title(call)
        task = AgentTask(
            id=uuid.uuid4().hex[:12],
            title=title,
            user_request=call.source_text or json.dumps(call.arguments, ensure_ascii=False),
        )
        self._tasks[task.id] = task
        self._emit(
            AgentEvent(
                type=AgentEventType.TASK_STARTED,
                task_id=task.id,
                title=task.title,
                message=f"开始调用工具：{definition.name if definition is not None else call.tool_name}。",
                metadata=self._tool_metadata(call),
            )
        )
        worker = threading.Thread(
            target=self._run_tool_request,
            args=(task.id, call),
            daemon=True,
        )
        worker.start()
        return task.id

    def drain_events(self) -> list[AgentEvent]:
        for bridge in self._bridges:
            for event in bridge.read_new_events():
                self._ingest_external_event(event)
        events: list[AgentEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def resolve_approval(self, task_id: str, approved: bool) -> None:
        task = self._tasks.get(task_id)
        title = task.title if task is not None else "授权请求"
        pending = self._pending_approvals.pop(task_id, None)
        if not approved:
            if task is not None:
                task.status = AgentEventType.TASK_FAILED
            self._emit(
                AgentEvent(
                    type=AgentEventType.TASK_FAILED,
                    task_id=task_id,
                    title=title,
                    message=f"{title} 已取消。",
                )
            )
            return

        if pending is None:
            if task is not None:
                task.status = AgentEventType.TASK_COMPLETED
            self._emit(
                AgentEvent(
                    type=AgentEventType.TASK_COMPLETED,
                    task_id=task_id,
                    title=title,
                    message=f"{title} 已确认授权。",
                )
            )
            return

        call = pending
        if task is not None:
            task.status = AgentEventType.TASK_PROGRESS
        self._emit(
            AgentEvent(
                type=AgentEventType.TASK_PROGRESS,
                task_id=task_id,
                title=title,
                message=f"{title} 已获授权，继续执行。",
                metadata=self._tool_metadata(call),
            )
        )
        worker = threading.Thread(
            target=self._run_approved_tool_request,
            args=(task_id, call),
            daemon=True,
        )
        worker.start()

    def _run_demo_task(self, task_id: str) -> None:
        task = self._tasks[task_id]
        time.sleep(0.8)
        task.status = AgentEventType.TASK_PROGRESS
        self._emit(
            AgentEvent(
                type=AgentEventType.TASK_PROGRESS,
                task_id=task.id,
                title=task.title,
                message=f"正在整理执行步骤：{task.title}。",
            )
        )
        time.sleep(1.0)
        lowered = task.user_request.lower()
        needs_approval = any(
            token in lowered
            for token in ("提权", "权限", "安装", "下载", "网络", "系统", "管理员", "approval")
        )
        if needs_approval:
            task.status = AgentEventType.APPROVAL_REQUIRED
            self._emit(
                AgentEvent(
                    type=AgentEventType.APPROVAL_REQUIRED,
                    task_id=task.id,
                    title=task.title,
                    message=f"{task.title} 需要授权后才能继续。",
                    approval=ApprovalRequest(
                        title=task.title,
                        detail=f"{task.title} 需要执行受限操作，请确认是否允许。",
                    ),
                )
            )
            return

        task.status = AgentEventType.TASK_COMPLETED
        self._emit(
            AgentEvent(
                type=AgentEventType.TASK_COMPLETED,
                task_id=task.id,
                title=task.title,
                message=f"{task.title} 已完成。",
            )
        )

    def _run_tool_request(self, task_id: str, call: ToolCall) -> None:
        task = self._tasks[task_id]
        time.sleep(0.2)
        task.status = AgentEventType.TASK_PROGRESS
        self._emit(
            AgentEvent(
                type=AgentEventType.TASK_PROGRESS,
                task_id=task.id,
                title=task.title,
                message=f"正在执行 {call.tool_name}。",
                metadata=self._tool_metadata(call),
            )
        )
        result = self._dispatch_tool(call)
        self._emit_tool_result(task, call, result)

    def _run_approved_tool_request(self, task_id: str, call: ToolCall) -> None:
        task = self._tasks[task_id]
        result = self._dispatch_tool(call, approved=True)
        self._emit_tool_result(task, call, result)

    def _dispatch_tool(self, call: ToolCall, approved: bool = False) -> ToolResult:
        return self._tools.execute_call(call, approved=approved)

    def _run_agent_plan(self, task_id: str, plan: AgentPlan) -> None:
        task = self._tasks[task_id]
        step_count = len(plan.steps)
        task.status = AgentEventType.TASK_PROGRESS
        self._emit(
            AgentEvent(
                type=AgentEventType.TASK_PROGRESS,
                task_id=task.id,
                title=task.title,
                message=f"已规划 {step_count} 个步骤，开始逐步执行。",
                metadata=self._agent_metadata(plan),
            )
        )

        details: list[str] = []
        artifacts: list[str] = []
        saw_observation = False
        for index, call in enumerate(plan.steps, start=1):
            task.status = AgentEventType.TASK_PROGRESS
            self._emit(
                AgentEvent(
                    type=AgentEventType.TASK_PROGRESS,
                    task_id=task.id,
                    title=task.title,
                    message=f"第 {index}/{step_count} 步：执行 {call.tool_name}。",
                    metadata={**self._tool_metadata(call), **self._agent_metadata(plan, step=index)},
                )
            )
            result = self._dispatch_tool(call)
            detail = result.detail.strip()
            details.append(
                "\n".join(
                    part
                    for part in (
                        f"## Step {index}: {call.tool_name}",
                        f"arguments: {json.dumps(call.arguments, ensure_ascii=False)}",
                        f"status: {result.status}",
                        f"summary: {result.summary}",
                        detail[:4000] if detail else "",
                    )
                    if part
                )
            )
            artifacts.extend(result.artifacts)
            if call.tool_name in {"browser.search_extract", "browser.observe"}:
                saw_observation = True

            if result.requires_approval:
                self._pending_approvals[task.id] = call
                task.status = AgentEventType.APPROVAL_REQUIRED
                self._emit(
                    AgentEvent(
                        type=AgentEventType.APPROVAL_REQUIRED,
                        task_id=task.id,
                        title=task.title,
                        message=f"Agent 任务在第 {index} 步需要授权。",
                        approval=ApprovalRequest(
                            title=task.title,
                            detail=result.detail or result.summary,
                            capability=f"tool:{call.tool_name}",
                        ),
                        metadata={
                            **self._tool_metadata(call),
                            **self._agent_metadata(plan, step=index),
                            "detail": result.detail[:8000],
                            "status": result.status,
                        },
                    )
                )
                return

            if not result.ok:
                task.status = AgentEventType.TASK_FAILED
                self._emit(
                    AgentEvent(
                        type=AgentEventType.TASK_FAILED,
                        task_id=task.id,
                        title=task.title,
                        message=f"Agent 任务在第 {index} 步失败：{result.summary}",
                        metadata={
                            "tool": "agent.plan",
                            "status": "failed",
                            "detail": "\n\n".join(details)[:12000],
                            "artifacts": "\n".join(artifacts[:12]),
                            **self._agent_metadata(plan, step=index),
                        },
                    )
                )
                return

        task.status = AgentEventType.TASK_COMPLETED
        metadata = {
            "tool": "agent.plan",
            "status": "success",
            "detail": "\n\n".join(details)[:12000],
            "artifacts": "\n".join(artifacts[:12]),
            **self._agent_metadata(plan),
        }
        if saw_observation:
            metadata["agent_observation"] = "true"
        self._emit(
            AgentEvent(
                type=AgentEventType.TASK_COMPLETED,
                task_id=task.id,
                title=task.title,
                message=f"Agent 任务完成，共执行 {step_count} 步。",
                metadata=metadata,
            )
        )

    def _emit_tool_result(self, task: AgentTask, call: ToolCall, result: ToolResult) -> None:
        metadata = {
            **self._tool_metadata(call),
            "detail": result.detail[:8000],
            "status": result.status,
        }
        if result.artifacts:
            metadata["artifacts"] = "\n".join(result.artifacts[:8])
        if result.requires_approval:
            self._pending_approvals[task.id] = call
            task.status = AgentEventType.APPROVAL_REQUIRED
            self._emit(
                AgentEvent(
                    type=AgentEventType.APPROVAL_REQUIRED,
                    task_id=task.id,
                    title=task.title,
                    message=result.summary,
                    approval=ApprovalRequest(
                        title=task.title,
                        detail=result.detail or result.summary,
                        capability=f"tool:{call.tool_name}",
                    ),
                    metadata=metadata,
                )
            )
            return
        if result.ok:
            task.status = AgentEventType.TASK_COMPLETED
            self._emit(
                AgentEvent(
                    type=AgentEventType.TASK_COMPLETED,
                    task_id=task.id,
                    title=task.title,
                    message=result.summary,
                    metadata=metadata,
                )
            )
            return
        task.status = AgentEventType.TASK_FAILED
        self._emit(
            AgentEvent(
                type=AgentEventType.TASK_FAILED,
                task_id=task.id,
                title=task.title,
                message=result.summary,
                metadata=metadata,
            )
        )

    def _emit(self, event: AgentEvent) -> None:
        self._events.put(event)

    def _ingest_external_event(self, event: AgentEvent) -> None:
        self._tasks[event.task_id] = AgentTask(
            id=event.task_id,
            title=event.title,
            user_request=event.message,
            status=event.type,
        )
        self._emit(event)

    @staticmethod
    def _task_title(user_request: str) -> str:
        cleaned = " ".join(user_request.strip().split())
        if not cleaned:
            return "未命名任务"
        return cleaned[:28] + ("…" if len(cleaned) > 28 else "")

    def _tool_title(self, call: ToolCall) -> str:
        definition = self._tools.get_definition(call.tool_name)
        name = definition.name if definition is not None else call.tool_name
        args = call.arguments
        subject = (
            args.get("pattern")
            or args.get("path")
            or args.get("command")
            or args.get("url")
            or args.get("target")
            or args.get("text")
            or args.get("query")
            or args.get("label")
            or call.source_text
            or name
        )
        return self._task_title(f"{name}: {subject}")

    @staticmethod
    def _tool_metadata(call: ToolCall) -> dict[str, str]:
        return {
            "tool": call.tool_name,
            "arguments": json.dumps(call.arguments, ensure_ascii=False),
        }

    def _build_agent_plan(self, user_text: str, force: bool = False) -> AgentPlan | None:
        cleaned = " ".join((user_text or "").strip().split())
        if not cleaned:
            return None
        segments = self._split_agent_segments(cleaned)
        calls: list[ToolCall] = []
        for segment in segments:
            call = self._tools.plan_natural_language(segment)
            if call is not None:
                calls.append(call)
        calls = self._dedupe_calls(calls)
        if force and len(calls) == 1:
            return AgentPlan(cleaned, calls)
        if len(calls) < 2:
            return None
        return AgentPlan(cleaned, calls)

    @staticmethod
    def _split_agent_segments(text: str) -> list[str]:
        connectors = r"(?:然后再|再帮我|再给我|然后|接着|顺便|并且|同时|之后|最后|再)"
        parts = [
            part.strip(" ，,；;。")
            for part in re.split(rf"\s*(?:[，,；;。]\s*)?{connectors}\s*", text)
            if part.strip(" ，,；;。")
        ]
        if len(parts) <= 1:
            return [text]
        enriched: list[str] = []
        previous_browser = False
        for part in parts:
            current = part
            if previous_browser and not any(token in current for token in ("浏览器", "网页", "页面", "网站")):
                if any(token in current for token in ("观察", "看看", "看到什么", "截图", "点击", "输入", "提取", "读取")):
                    current = f"浏览器{current}"
            enriched.append(current)
            previous_browser = previous_browser or any(token in current for token in ("浏览器", "网页", "页面", "网站"))
        return enriched

    @staticmethod
    def _dedupe_calls(calls: list[ToolCall]) -> list[ToolCall]:
        deduped: list[ToolCall] = []
        seen: set[tuple[str, str]] = set()
        for call in calls:
            key = (call.tool_name, json.dumps(call.arguments, ensure_ascii=False, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(call)
        return deduped

    @staticmethod
    def _agent_metadata(plan: AgentPlan, step: int | None = None) -> dict[str, str]:
        rows = [
            {"index": index, "tool": call.tool_name, "arguments": call.arguments}
            for index, call in enumerate(plan.steps, start=1)
        ]
        metadata = {
            "agent_plan": json.dumps(rows, ensure_ascii=False),
            "agent_step_count": str(len(plan.steps)),
        }
        if step is not None:
            metadata["agent_step"] = str(step)
        return metadata
