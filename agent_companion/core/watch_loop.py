from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
import uuid
from typing import Callable

from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, VoiceLine


@dataclass(frozen=True)
class WatchLoopOptions:
    query: str = "陪我看当前视频"
    interval_seconds: float = 6.0
    sample_count: int = 3
    sample_interval_ms: int = 700
    transcript_source: str = "system_audio"
    transcribe: bool = True


@dataclass(frozen=True)
class WatchLoopTick:
    ok: bool
    summary: str
    transcript_text: list[str] = field(default_factory=list)
    transcript_source: str = ""
    transcript_status: str = ""
    rolling_summary: str = ""
    rolling_transcript: list[str] = field(default_factory=list)
    transcript_window_seconds: int = 0
    source_health: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class WatchLoopSnapshot:
    active: bool = False
    session_id: str = ""
    query: str = ""
    interval_seconds: float = 6.0
    sample_count: int = 3
    transcript_source: str = "system_audio"
    transcript_status: str = ""
    iterations: int = 0
    started_at: float = 0.0
    updated_at: float = 0.0
    last_summary: str = ""
    last_transcript: list[str] = field(default_factory=list)
    rolling_summary: str = ""
    rolling_transcript: list[str] = field(default_factory=list)
    transcript_window_seconds: int = 0
    source_health: dict = field(default_factory=dict)
    last_error: str = ""

    def to_agent_state(self) -> dict:
        return {
            "active": self.active,
            "session_id": self.session_id,
            "query": self.query,
            "interval_seconds": round(float(self.interval_seconds or 0), 2),
            "sample_count": int(self.sample_count or 0),
            "transcript_source": self.transcript_source,
            "transcript_status": self.transcript_status,
            "iterations": int(self.iterations or 0),
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "last_summary": self.last_summary[:300],
            "last_transcript": list(self.last_transcript[:8]),
            "rolling_summary": self.rolling_summary[:500],
            "rolling_transcript": list(self.rolling_transcript[:12]),
            "transcript_window_seconds": int(self.transcript_window_seconds or 0),
            "source_health": self.source_health,
            "last_error": self.last_error,
        }


class WatchLoopController:
    def __init__(
        self,
        tick: Callable[[WatchLoopOptions], WatchLoopTick],
        emit: Callable[[AgentEvent], None],
    ) -> None:
        self._tick = tick
        self._emit = emit
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._options = WatchLoopOptions()
        self._snapshot = WatchLoopSnapshot()

    def start(self, options: WatchLoopOptions | None = None) -> WatchLoopSnapshot:
        options = _normalize_options(options or WatchLoopOptions())
        self.stop(emit=False)
        session_id = f"watch-{uuid.uuid4().hex[:10]}"
        now = time.time()
        with self._lock:
            self._options = options
            self._stop_event = threading.Event()
            self._snapshot = WatchLoopSnapshot(
                active=True,
                session_id=session_id,
                query=options.query,
                interval_seconds=options.interval_seconds,
                sample_count=options.sample_count,
                transcript_source=options.transcript_source,
                started_at=now,
                updated_at=now,
            )
        self._emit_event("实时陪看已启动。", status="success")
        self._tick_once(session_id)
        thread = threading.Thread(target=self._run, args=(session_id,), daemon=True)
        with self._lock:
            if self._snapshot.active and self._snapshot.session_id == session_id:
                self._thread = thread
                thread.start()
        return self.snapshot()

    def stop(self, *, emit: bool = True, reason: str = "实时陪看已停止。") -> WatchLoopSnapshot:
        thread: threading.Thread | None = None
        with self._lock:
            was_active = self._snapshot.active
            self._stop_event.set()
            thread = self._thread
            self._thread = None
            if was_active:
                self._snapshot.active = False
                self._snapshot.updated_at = time.time()
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if emit and was_active:
            self._emit_event(reason, status="info")
        return self.snapshot()

    def snapshot(self) -> WatchLoopSnapshot:
        with self._lock:
            snap = self._snapshot
            return WatchLoopSnapshot(
                active=snap.active,
                session_id=snap.session_id,
                query=snap.query,
                interval_seconds=snap.interval_seconds,
                sample_count=snap.sample_count,
                transcript_source=snap.transcript_source,
                transcript_status=snap.transcript_status,
                iterations=snap.iterations,
                started_at=snap.started_at,
                updated_at=snap.updated_at,
                last_summary=snap.last_summary,
                last_transcript=list(snap.last_transcript),
                rolling_summary=snap.rolling_summary,
                rolling_transcript=list(snap.rolling_transcript),
                transcript_window_seconds=snap.transcript_window_seconds,
                source_health=dict(snap.source_health),
                last_error=snap.last_error,
            )

    def _run(self, session_id: str) -> None:
        while True:
            with self._lock:
                options = self._options
                stop_event = self._stop_event
            if stop_event.wait(max(1.0, float(options.interval_seconds or 6.0))):
                return
            if not self._is_current(session_id):
                return
            self._tick_once(session_id)

    def _tick_once(self, session_id: str) -> None:
        if not self._is_current(session_id):
            return
        with self._lock:
            options = self._options
        try:
            tick = self._tick(options)
        except Exception as exc:
            tick = WatchLoopTick(False, "实时陪看采样失败。", error=_safe_error(type(exc).__name__))
        with self._lock:
            if not self._snapshot.active or self._snapshot.session_id != session_id:
                return
            self._snapshot.iterations += 1
            self._snapshot.updated_at = time.time()
            self._snapshot.last_summary = tick.summary
            self._snapshot.last_transcript = list(tick.transcript_text[:8])
            self._snapshot.rolling_summary = tick.rolling_summary
            self._snapshot.rolling_transcript = list(tick.rolling_transcript[:12])
            self._snapshot.transcript_window_seconds = tick.transcript_window_seconds
            self._snapshot.source_health = dict(tick.source_health)
            self._snapshot.transcript_source = tick.transcript_source or self._options.transcript_source
            self._snapshot.transcript_status = tick.transcript_status
            self._snapshot.last_error = tick.error
        self._emit_event(tick.summary or "实时陪看上下文已更新。", status="success" if tick.ok else "failed")

    def _is_current(self, session_id: str) -> bool:
        with self._lock:
            return self._snapshot.active and self._snapshot.session_id == session_id and not self._stop_event.is_set()

    def _emit_event(self, summary: str, *, status: str = "info") -> None:
        snapshot = self.snapshot()
        body_lines = [
            f"状态：{'运行中' if snapshot.active else '已停止'}",
            f"采样：{snapshot.iterations} 次，每 {snapshot.interval_seconds:g}s",
        ]
        if snapshot.transcript_source:
            body_lines.append(f"转写源：{snapshot.transcript_source}")
        if snapshot.last_transcript:
            body_lines.append("最近转写：" + " / ".join(snapshot.last_transcript[:4]))
        if snapshot.rolling_summary:
            body_lines.append(f"滚动摘要：{snapshot.rolling_summary}")
        if snapshot.last_error:
            body_lines.append(f"状态码：{snapshot.last_error}")
        self._emit(
            AgentEvent(
                EventType.AUDIT_EVENT,
                snapshot.session_id or f"watch-{uuid.uuid4().hex[:8]}",
                DisplayCard("实时陪看", summary or "实时陪看状态已更新。", "\n".join(body_lines), status=status),
                VoiceLine(""),
                {"tool": "watch.loop", "watch_loop": snapshot.to_agent_state()},
            )
        )


def _normalize_options(options: WatchLoopOptions) -> WatchLoopOptions:
    query = " ".join((options.query or "陪我看当前视频").split())[:180]
    source = (options.transcript_source or "auto").strip().casefold()
    if source not in {"auto", "ocr_subtitle", "system_audio"}:
        source = "auto"
    return WatchLoopOptions(
        query=query or "陪我看当前视频",
        interval_seconds=max(2.0, min(60.0, float(options.interval_seconds or 6.0))),
        sample_count=max(1, min(8, int(options.sample_count or 3))),
        sample_interval_ms=max(0, min(2500, int(options.sample_interval_ms or 700))),
        transcript_source=source,
        transcribe=bool(options.transcribe),
    )


def _safe_error(value: str) -> str:
    text = (value or "").strip().casefold()
    if not text:
        return ""
    return text[:48] if text.replace("_", "").isalnum() else "watch_loop_failed"
