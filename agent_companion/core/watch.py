from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import threading
import time
from typing import Any


@dataclass(frozen=True)
class WatchFrame:
    user_question: str
    summary: str
    title: str = ""
    artifact: str = ""
    model_status: str = "unknown"
    ocr_summary: str = ""
    ocr_text: list[str] = field(default_factory=list)
    ocr_regions: list[dict[str, Any]] = field(default_factory=list)
    transcript_text: list[str] = field(default_factory=list)
    transcript_source: str = ""
    transcript_status: str = ""
    sequence_summary: str = ""
    frame_index: int = 1
    sequence_size: int = 1
    created_at: float = field(default_factory=time.time)

    def to_agent_state(self) -> dict[str, Any]:
        return {
            "user_question": self.user_question,
            "summary": self.summary,
            "title": self.title,
            "artifact": self.artifact,
            "model_status": self.model_status,
            "ocr_summary": self.ocr_summary,
            "ocr_text": list(self.ocr_text[:12]),
            "ocr_regions": list(self.ocr_regions[:8]),
            "transcript_text": list(self.transcript_text[:12]),
            "transcript_source": self.transcript_source,
            "transcript_status": self.transcript_status,
            "sequence_summary": self.sequence_summary,
            "frame_index": self.frame_index,
            "sequence_size": self.sequence_size,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class WatchTranscriptEntry:
    text: str
    source: str = ""
    status: str = ""
    title: str = ""
    observed_at: float = field(default_factory=time.time)

    def to_agent_state(self) -> dict[str, Any]:
        return {
            "text": self.text[:240],
            "source": self.source,
            "status": self.status,
            "title": self.title[:120],
            "observed_at": self.observed_at,
        }


class WatchSession:
    def __init__(self, limit: int = 5, transcript_limit: int = 80, transcript_window_seconds: int = 300) -> None:
        self.limit = max(1, limit)
        self.transcript_limit = max(8, transcript_limit)
        self.transcript_window_seconds = max(30, transcript_window_seconds)
        self._frames: list[WatchFrame] = []
        self._transcripts: list[WatchTranscriptEntry] = []
        self._transcript_summary = ""
        self._transcript_updated_at = 0.0
        self._lock = threading.RLock()

    def add(self, frame: WatchFrame) -> None:
        if not frame.summary and not frame.artifact and not frame.transcript_text:
            return
        with self._lock:
            self._frames.append(frame)
            self._frames = self._frames[-self.limit :]
            self._add_transcripts_locked(frame)

    def recent(self, limit: int = 3) -> list[WatchFrame]:
        with self._lock:
            return list(reversed(self._frames[-max(1, limit) :]))

    def recent_with_transcript(self, limit: int = 3) -> list[WatchFrame]:
        with self._lock:
            frames = list(reversed(self._frames[-max(1, limit) :]))
            transcript_frame = self._transcript_context_frame_locked()
        if transcript_frame is None:
            return frames
        return [transcript_frame, *frames]

    def has_context(self) -> bool:
        with self._lock:
            return bool(self._frames or self._transcripts)

    def transcript_state(self) -> dict[str, Any]:
        with self._lock:
            self._trim_transcripts_locked()
            entries = list(self._transcripts)
            if not entries:
                self._transcript_summary = ""
            return {
                "status": "success" if entries else "empty",
                "window_seconds": self.transcript_window_seconds,
                "segment_count": len(entries),
                "summary": self._transcript_summary,
                "updated_at": self._transcript_updated_at,
                "source": _dominant_transcript_source(entries),
                "recent_text": [entry.text[:180] for entry in entries[-12:]],
                "source_health": _transcript_source_health(entries),
            }

    def _add_transcripts_locked(self, frame: WatchFrame) -> None:
        source = frame.transcript_source or "unknown"
        status = frame.transcript_status or "unknown"
        existing = {_normalize_transcript_text(entry.text) for entry in self._transcripts}
        added = False
        for text in frame.transcript_text:
            clean = _clean_transcript_text(text)
            key = _normalize_transcript_text(clean)
            if not clean or key in existing:
                continue
            existing.add(key)
            self._transcripts.append(
                WatchTranscriptEntry(
                    text=clean,
                    source=source,
                    status=status,
                    title=frame.title,
                    observed_at=frame.created_at,
                )
            )
            added = True
        self._trim_transcripts_locked()
        if added:
            self._transcript_summary = _rolling_transcript_summary(self._transcripts, self.transcript_window_seconds)
            self._transcript_updated_at = time.time()

    def _trim_transcripts_locked(self) -> None:
        cutoff = time.time() - self.transcript_window_seconds
        self._transcripts = [entry for entry in self._transcripts if entry.observed_at >= cutoff]
        if len(self._transcripts) > self.transcript_limit:
            self._transcripts = self._transcripts[-self.transcript_limit :]

    def _transcript_context_frame_locked(self) -> WatchFrame | None:
        self._trim_transcripts_locked()
        if not self._transcripts:
            return None
        entries = self._transcripts[-24:]
        summary = self._transcript_summary or _rolling_transcript_summary(self._transcripts, self.transcript_window_seconds)
        return WatchFrame(
            user_question="实时陪看滚动转写",
            summary=summary,
            title=entries[-1].title,
            model_status="transcript_memory",
            transcript_text=[entry.text for entry in entries],
            transcript_source=_dominant_transcript_source(entries),
            transcript_status="success",
            sequence_summary=summary,
            created_at=entries[-1].observed_at,
        )


def answer_from_recent_frames(question: str, frames: list[WatchFrame]) -> tuple[str, str]:
    if not frames:
        return "我还没有最近的画面上下文。先让我看一下当前窗口吧。", ""
    latest = frames[0]
    transcript_frame = _best_transcript_frame(frames)
    visual_frame = _best_visual_summary_frame(frames)
    region_frame = _best_region_frame(frames)
    title = f"《{latest.title}》" if latest.title else "刚才的画面"
    if latest.model_status == "unconfigured":
        return f"{title}的截图已经保存了，但还没有配置视觉模型，所以我现在只能确认画面已记录。", "vision_unconfigured"
    if latest.model_status == "error":
        return f"{title}的截图已经保存了，不过视觉摘要暂时没生成出来。", "vision_error"
    ocr_line = _ocr_line(latest)
    transcript_line = _transcript_line(transcript_frame or latest)
    question_hint = question or ""
    region_answer = _region_answer(question_hint, region_frame or latest)
    if region_answer:
        return region_answer, "vision_context"
    if transcript_line and _asks_video_content(question_hint):
        sequence_summary = (transcript_frame or latest).sequence_summary
        if visual_frame and visual_frame.sequence_summary and visual_frame.sequence_summary != sequence_summary:
            return f"从画面看，{visual_frame.sequence_summary}。转写里能读到/听到：{transcript_line}", "vision_context"
        if visual_frame and visual_frame.summary and visual_frame.summary != sequence_summary:
            return f"从画面看，{visual_frame.summary}。转写里能读到/听到：{transcript_line}", "vision_context"
        prefix = f"从连续画面看，{sequence_summary}" if sequence_summary else "根据实时转写"
        return f"{prefix}。转写里能读到/听到：{transcript_line}", "vision_context"
    if latest.sequence_summary and _asks_video_content(question_hint):
        suffix = f" 可见文字/弹幕线索包括：{ocr_line}" if ocr_line else ""
        return f"从连续画面看，{latest.sequence_summary}{suffix}", "vision_context"
    if any(token in question_hint for token in ("写了什么", "文字", "按钮", "页面里", "标题", "label", "button")) and ocr_line:
        return f"{title}里我能读到这些可见文字：{ocr_line}", "vision_context"
    if len(frames) == 1:
        suffix = f" 可见文字包括：{ocr_line}" if ocr_line else ""
        return f"刚才我看到的是：{latest.summary}{suffix}", "vision_context"
    prior = "；".join(_frame_context(frame) for frame in frames[:3] if frame.summary or frame.ocr_text)
    return f"结合最近几次画面，我看到的重点是：{prior}", "vision_context"


class WatchAnswerer:
    def __init__(self, workspace: Path, character_name: str = "Joi", character_persona: str = "") -> None:
        self.workspace = workspace.resolve()
        self.character_name = character_name
        self.character_persona = character_persona
        self._config: Any | None = self._load_config()
        self._client: Any | None = None
        self.last_used_model = False
        self.last_model_usage: dict[str, Any] | None = None

    def answer(self, question: str, frames: list[WatchFrame]) -> tuple[str, str]:
        fallback, status = answer_from_recent_frames(question, frames)
        self.last_used_model = False
        self.last_model_usage = None
        if not frames or os.environ.get("AGENT_COMPANION_DISABLE_LLM") == "1":
            return fallback, status
        config = self._config
        if config is None or config.llm.use_mock or not (config.llm.is_expression_configured or config.llm.is_configured):
            return fallback, status
        try:
            from openai import OpenAI

            from agent_companion.core.config import ModelRouter

            router = ModelRouter(config.llm)
            endpoint = router.resolve("summarize")
            if self._client is None or self._client.base_url != endpoint.base_url:
                self._client = OpenAI(api_key=endpoint.api_key, base_url=endpoint.base_url)
            character = config.primary_character if config.characters else None
            character_name = character.name if character else self.character_name
            persona = character.setting if character else self.character_persona
            context = [
                {
                    "title": frame.title,
                    "summary": frame.summary,
                    "ocr_summary": frame.ocr_summary,
                    "ocr_text": frame.ocr_text[:10],
                    "ocr_regions": frame.ocr_regions[:5],
                    "transcript_text": frame.transcript_text[:24] if frame.model_status == "transcript_memory" else frame.transcript_text[:10],
                    "transcript_source": frame.transcript_source,
                    "transcript_status": frame.transcript_status,
                    "sequence_summary": frame.sequence_summary,
                    "frame_index": frame.frame_index,
                    "sequence_size": frame.sequence_size,
                    "user_question": frame.user_question,
                    "model_status": frame.model_status,
                    "age_seconds": int(time.time() - frame.created_at),
                }
                for frame in frames[:3]
            ]
            started = time.perf_counter()
            response = self._client.chat.completions.create(
                model=endpoint.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"你是{character_name}，正在陪用户看当前窗口、网页或视频。\n"
                            f"角色设定：{persona[:1800]}\n"
                            "根据最近视觉上下文回答用户追问。要自然、具体，不要像工具日志。"
                            "只输出 JSON：{\"answer\":\"给 UI 显示的自然回答\"}。"
                            "禁止输出 JSON 以外文本，禁止包含截图路径、模型名、工具名、task id、命令、token 或日志。"
                            "如果视觉上下文不足，要坦率说明需要再看一次。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps({"question": question, "recent_frames": context}, ensure_ascii=False),
                    },
                ],
                temperature=min(max(config.llm.temperature, 0.2), 0.9),
                response_format={"type": "json_object"},
            )
            latency_ms = (time.perf_counter() - started) * 1000
            payload = json.loads(response.choices[0].message.content or "{}")
            answer = str(payload.get("answer") or "").strip()
            if not answer:
                return fallback, status
            self.last_used_model = True
            self.last_model_usage = endpoint.to_agent_state(latency_ms=latency_ms)
            return answer[:900], "llm_answer"
        except Exception:
            return fallback, status

    def _load_config(self) -> Any | None:
        config_path = self.workspace / "config.yaml"
        if not config_path.is_file():
            return None
        try:
            from agent_companion.core.config import load_app_config

            return load_app_config(config_path)
        except Exception:
            return None


def _ocr_line(frame: WatchFrame) -> str:
    snippets = [text.strip() for text in frame.ocr_text[:8] if text.strip()]
    if snippets:
        return "、".join(snippets)
    return frame.ocr_summary.strip()


def _transcript_line(frame: WatchFrame) -> str:
    snippets = [text.strip() for text in frame.transcript_text[:8] if text.strip()]
    return "、".join(snippets)


def _best_transcript_frame(frames: list[WatchFrame]) -> WatchFrame | None:
    for frame in frames:
        if frame.model_status == "transcript_memory" and frame.transcript_text:
            return frame
    for frame in frames:
        if frame.transcript_text:
            return frame
    return None


def _best_region_frame(frames: list[WatchFrame]) -> WatchFrame | None:
    for frame in frames:
        if frame.ocr_regions:
            return frame
    return None


def _best_visual_summary_frame(frames: list[WatchFrame]) -> WatchFrame | None:
    for frame in frames:
        if frame.model_status == "ok" and (frame.sequence_summary or frame.summary):
            return frame
    return None


def _asks_video_content(question: str) -> bool:
    return any(
        token in (question or "")
        for token in ("视频", "播放", "弹幕", "字幕", "讲什么", "讲了什么", "在讲", "内容", "关于什么", "发生了什么", "这一段", "这段", "讲到哪", "说了什么", "正在播放")
    )


def _region_answer(question: str, frame: WatchFrame) -> str:
    if not frame.ocr_regions:
        return ""
    if any(token in question for token in ("右上角", "右上", "页面右上", "右侧上方")):
        text = _region_item_text(frame, horizontal="right", vertical="top")
        if text:
            return f"右上角附近我能看到：{text}"
    if any(token in question for token in ("左上角", "左上", "页面左上", "左侧上方")):
        text = _region_item_text(frame, horizontal="left", vertical="top")
        if text:
            return f"左上角附近我能看到：{text}"
    if any(token in question for token in ("有哪些按钮", "按钮", "控件")):
        text = _region_item_text(frame)
        if text:
            return f"我能看到这些可能的按钮或标签：{text}"
    return ""


def _region_item_text(frame: WatchFrame, horizontal: str = "", vertical: str = "") -> str:
    snippets: list[str] = []
    for region in frame.ocr_regions:
        if not isinstance(region, dict):
            continue
        for item in region.get("items") or []:
            if not isinstance(item, dict):
                continue
            if horizontal and item.get("horizontal") != horizontal:
                continue
            if vertical and item.get("vertical") != vertical:
                continue
            text = str(item.get("text") or "").strip()
            if text and text not in snippets:
                snippets.append(text[:48])
            if len(snippets) >= 8:
                break
    return "、".join(snippets[:8])


def _frame_context(frame: WatchFrame) -> str:
    transcript = _transcript_line(frame)
    if frame.sequence_summary:
        base = frame.sequence_summary.strip()
        ocr = _ocr_line(frame)
        detail = transcript or ocr
        return f"{base}；转写/文字：{detail}" if detail else base
    base = frame.summary.strip()
    ocr = _ocr_line(frame)
    if base and transcript:
        return f"{base}；转写：{transcript}"
    if base and ocr:
        return f"{base}；可见文字：{ocr}"
    if transcript:
        return f"转写：{transcript}"
    return base or f"可见文字：{ocr}"


def _clean_transcript_text(text: str) -> str:
    return " ".join((text or "").replace("\u3000", " ").split()).strip()


def _normalize_transcript_text(text: str) -> str:
    value = _clean_transcript_text(text).casefold()
    return "".join(ch for ch in value if not ch.isspace())


def _dominant_transcript_source(entries: list[WatchTranscriptEntry]) -> str:
    counts: dict[str, int] = {}
    for entry in entries:
        source = entry.source or "unknown"
        counts[source] = counts.get(source, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: item[1])[0]


def _transcript_source_health(entries: list[WatchTranscriptEntry]) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for entry in entries:
        source = entry.source or "unknown"
        row = rows.setdefault(source, {"count": 0, "last_seen_at": 0.0, "status": entry.status or "unknown"})
        row["count"] += 1
        row["last_seen_at"] = max(float(row["last_seen_at"]), entry.observed_at)
        if entry.status:
            row["status"] = entry.status
    return rows


def _rolling_transcript_summary(entries: list[WatchTranscriptEntry], window_seconds: int) -> str:
    if not entries:
        return ""
    recent = [_clean_transcript_text(entry.text) for entry in entries[-10:] if _clean_transcript_text(entry.text)]
    if not recent:
        return ""
    minutes = max(1, round(window_seconds / 60))
    compact: list[str] = []
    seen: set[str] = set()
    for text in recent:
        key = _normalize_transcript_text(text)
        if key in seen:
            continue
        seen.add(key)
        compact.append(text[:80])
    return f"最近 {minutes} 分钟的转写线索：" + " / ".join(compact[-6:])
