from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BrowserRequest:
    id: str
    action: str
    arguments: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    status: str = "queued"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "arguments": self.arguments,
            "created_at": self.created_at,
            "status": self.status,
        }


class BrowserBridge:
    """Local browser executor bridge using append-only request/response JSONL files."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.event_dir = self.workspace / "data" / "agent_events"
        self.request_path = self.event_dir / "browser_requests.jsonl"
        self.response_path = self.event_dir / "browser_responses.jsonl"
        self.ready_path = self.event_dir / "browser_executor.ready"
        self.pid_path = self.event_dir / "browser_executor.pid"
        self._lock = threading.Lock()

    def queue_request(self, action: str, arguments: dict[str, Any]) -> BrowserRequest:
        request = BrowserRequest(
            id=f"browser-{uuid.uuid4().hex[:10]}",
            action=action,
            arguments=arguments,
        )
        self.request_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.request_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(request.to_dict(), ensure_ascii=False) + "\n")
        return request

    def execute(self, action: str, arguments: dict[str, Any], timeout: float = 45.0) -> dict[str, Any]:
        self.ensure_executor()
        request = self.queue_request(action, arguments)
        return self.wait_for_response(request.id, timeout=timeout)

    def ensure_executor(self) -> None:
        self.event_dir.mkdir(parents=True, exist_ok=True)
        if self._executor_ready():
            return
        script = self.workspace / "run_browser_executor.py"
        if not script.is_file():
            raise RuntimeError(f"浏览器执行器脚本不存在: {script}")
        log_path = self.event_dir / "browser_executor.log"
        log = log_path.open("a", encoding="utf-8")
        try:
            subprocess.Popen(
                [sys.executable, str(script), "--workspace", str(self.workspace)],
                cwd=str(self.workspace),
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        finally:
            log.close()
        deadline = time.time() + 15
        while time.time() < deadline:
            if self._executor_ready():
                return
            time.sleep(0.2)
        raise RuntimeError("本地浏览器执行器启动超时。")

    def wait_for_response(self, request_id: str, timeout: float = 45.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = self._find_response(request_id)
            if response is not None:
                return response
            time.sleep(0.2)
        raise TimeoutError(f"浏览器执行超时: {request_id}")

    def _executor_ready(self) -> bool:
        if not self.ready_path.is_file() or not self.pid_path.is_file():
            return False
        try:
            pid = int(self.pid_path.read_text(encoding="utf-8").strip())
        except Exception:
            return False
        return self._pid_is_alive(pid)

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
            except Exception:
                return False
            synchronize = 0x00100000
            wait_timeout = 0x00000102
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(synchronize, False, pid)
            if not handle:
                return False
            try:
                return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
            finally:
                kernel32.CloseHandle(handle)
        try:
            import os

            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def _find_response(self, request_id: str) -> dict[str, Any] | None:
        if not self.response_path.is_file():
            return None
        try:
            lines = self.response_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return None
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if payload.get("request_id") == request_id:
                return payload
        return None
