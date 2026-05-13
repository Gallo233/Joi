from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable

from app2.events import AppEvent, TaskStatus, voice_line
from mvp.config import AppConfig


EventCallback = Callable[[AppEvent], None]


class CodexRunner:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._workspace = config.base_dir
        self._run_dir = self._workspace / "data" / "app2" / "codex_runs"

    def run(self, goal: str, emit: EventCallback) -> None:
        goal = " ".join((goal or "").strip().split())
        task_id = f"codex2-{uuid.uuid4().hex[:10]}"
        active_lang = self._config.primary_voice_text_lang()
        started = time.time()
        self._run_dir.mkdir(parents=True, exist_ok=True)

        emit(
            AppEvent(
                task_id=task_id,
                goal=goal,
                status=TaskStatus.STARTING,
                display_text="我开始处理这个任务了。",
                voice_text=voice_line(active_lang, "我开始处理这个任务了。", "確認してみるね。少し待ってて。"),
                details=self._task_details(goal, "准备启动 Codex。"),
            )
        )

        codex = self._codex_executable()
        if not codex:
            emit(
                AppEvent(
                    task_id=task_id,
                    goal=goal,
                    status=TaskStatus.FAILED,
                    display_text="我没有找到本地 Codex，任务没有启动。",
                    voice_text=voice_line(active_lang, "我没有找到本地 Codex，任务没有启动。", "Codexが見つからなくて、開始できなかったよ。"),
                    details=self._task_details(goal, "没有找到 codex CLI。请确认 Codex 已安装并在 PATH 中。"),
                    elapsed_seconds=time.time() - started,
                )
            )
            return

        stdout_path = self._run_dir / f"{task_id}.stdout.jsonl"
        stderr_path = self._run_dir / f"{task_id}.stderr.log"
        final_path = self._run_dir / f"{task_id}.final.txt"

        command = [
            codex,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--cd",
            str(self._workspace),
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            "--output-last-message",
            str(final_path),
            goal,
        ]

        emit(
            AppEvent(
                task_id=task_id,
                goal=goal,
                status=TaskStatus.RUNNING,
                display_text="Codex 已经启动，我在等它回传结果。",
                voice_text=voice_line(active_lang, "Codex 已经启动，我在等它回传结果。", "Codexを起動したよ。結果を待っているね。"),
                details=self._task_details(goal, "Codex 正在执行。", stdout_path, stderr_path),
                artifacts=[self._relative(stdout_path), self._relative(stderr_path), self._relative(final_path)],
            )
        )

        try:
            with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
                result = subprocess.run(
                    command,
                    cwd=str(self._workspace),
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                )
        except Exception as exc:
            emit(
                AppEvent(
                    task_id=task_id,
                    goal=goal,
                    status=TaskStatus.FAILED,
                    display_text="Codex 启动失败，详情我放在任务卡里了。",
                    voice_text=voice_line(active_lang, "Codex 启动失败，详情我放在任务卡里了。", "Codexの起動に失敗したみたい。詳細はカードにまとめたよ。"),
                    details=self._task_details(goal, f"启动异常：{exc}", stdout_path, stderr_path),
                    elapsed_seconds=time.time() - started,
                )
            )
            return

        elapsed = time.time() - started
        final_message = self._read_text(final_path).strip()
        stderr_tail = self._read_text(stderr_path).strip()[-1800:]
        if result.returncode == 0:
            emit(
                AppEvent(
                    task_id=task_id,
                    goal=goal,
                    status=TaskStatus.COMPLETED,
                    display_text="Codex 跑完了，结果在任务卡里。",
                    voice_text=voice_line(active_lang, "Codex 跑完了，结果在任务卡里。", "終わったよ。結果はカードにまとめておいた。"),
                    details=self._task_details(goal, final_message or "Codex 已完成，但没有写入最终摘要。", stdout_path, stderr_path, final_path),
                    final_message=final_message,
                    artifacts=[self._relative(stdout_path), self._relative(stderr_path), self._relative(final_path)],
                    elapsed_seconds=elapsed,
                )
            )
            return

        permission_hint = self._looks_like_permission_issue(stderr_tail)
        display_text = "这一步需要你手动处理权限，详情在任务卡里。" if permission_hint else "Codex 没有跑通，详情在任务卡里。"
        voice_text = (
            "権限の確認が必要みたい。詳細はカードにまとめたよ。"
            if permission_hint
            else "うまくいかなかったみたい。詳細はカードにまとめたよ。"
        )
        emit(
            AppEvent(
                task_id=task_id,
                goal=goal,
                status=TaskStatus.FAILED,
                display_text=display_text,
                voice_text=voice_line(active_lang, display_text, voice_text),
                details=self._task_details(
                    goal,
                    "\n".join(part for part in (f"退出码：{result.returncode}", final_message, stderr_tail) if part),
                    stdout_path,
                    stderr_path,
                    final_path,
                ),
                final_message=final_message,
                artifacts=[self._relative(stdout_path), self._relative(stderr_path), self._relative(final_path)],
                elapsed_seconds=elapsed,
            )
        )

    def _codex_executable(self) -> str:
        override = os.environ.get("SHINSEKAI_CODEX_BIN", "").strip()
        if override:
            path = Path(override)
            return str(path) if path.is_file() else ""
        return shutil.which("codex") or ""

    def _task_details(self, goal: str, summary: str, *paths: Path) -> str:
        lines = [
            f"目标：{goal}",
            f"状态：{summary.strip() or '处理中'}",
        ]
        existing = [path for path in paths if path]
        if existing:
            lines.append("")
            lines.append("相关文件：")
            lines.extend(f"- {self._relative(path)}" for path in existing)
        return "\n".join(lines)

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self._workspace).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _read_text(path: Path) -> str:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _looks_like_permission_issue(text: str) -> bool:
        lowered = text.casefold()
        return any(token in lowered for token in ("permission", "approval", "denied", "sandbox", "not permitted", "权限", "授权"))

