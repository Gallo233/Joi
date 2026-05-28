from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from agent_companion.core.computer_use import ComputerUseBackend, WindowsComputerUseBackend
from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult, VoiceLine
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.vision import OcrExtractor, PytesseractOcrExtractor, VisionObserver, VisionSummarizer, WindowsScreenObserver
from agent_companion.core.vision.ocr import OcrResult, run_ocr_safely
from agent_companion.core.vision.regions import group_ocr_regions, regions_to_agent_state, summarize_ocr_regions
from agent_companion.core.vision.schemas import VisionObservation
from agent_companion.core.voice import safe_voice_line
from agent_companion.core.watch_transcript import AudioTranscriptProvider, TranscriptResult, transcript_from_ocr_records


class ScreenObserveTool(ToolAdapter):
    name = "observe.screen"

    def __init__(
        self,
        workspace: Path,
        observer: VisionObserver | None = None,
        computer_backend: ComputerUseBackend | None = None,
        summarizer: VisionSummarizer | None = None,
        ocr: OcrExtractor | None = None,
        audio_transcriber: AudioTranscriptProvider | None = None,
    ) -> None:
        self.workspace = workspace
        self.observer = observer or WindowsScreenObserver(workspace)
        self.computer_backend = computer_backend or WindowsComputerUseBackend(workspace, self.observer)
        self.summarizer = summarizer
        self.ocr = ocr or PytesseractOcrExtractor()
        self.audio_transcriber = audio_transcriber

    def run(self, request: ToolRequest) -> ToolResult:
        query = str(request.arguments.get("query") or "").strip()
        target = self._target_from_request(request)
        sample_count = self._sample_count_from_request(request)
        sample_interval_ms = self._sample_interval_ms_from_request(request)
        try:
            records = self._capture_records(target, query, sample_count, sample_interval_ms)
        except Exception as exc:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "target": target, "error": type(exc).__name__, "detail": str(exc)[:500]},
                display_card=DisplayCard("画面观察", "当前画面没有截取成功。", str(exc)[:1800], status="failed"),
                voice_line=safe_voice_line("我没能截到当前画面，细节在卡片里。", sprite="4"),
            )

        first_record = records[0]
        latest_record = records[-1]
        computer_observation = latest_record["computer_observation"]
        observation = latest_record["observation"]
        ocr_result = latest_record["ocr_result"]
        ocr_regions = latest_record["ocr_regions"]
        region_summary = summarize_ocr_regions(ocr_regions)
        transcript = self._transcribe_records(records, request, sample_interval_ms)
        summary_text = ""
        summary_error = ""
        model_status = "unconfigured"
        if self.summarizer is not None:
            try:
                vs = self._summarize_records(records, query, transcript)
                summary_text = (vs.text or "").strip()
                summary_error = vs.error
                model_status = "ok" if summary_text else "error"
                if not summary_text and not summary_error:
                    summary_error = "empty vision summary"
            except Exception as exc:
                summary_error = f"{type(exc).__name__}: {exc}"
                model_status = "error"

        detail_lines = [
            f"窗口：{observation.title or ('全屏' if observation.target == 'fullscreen' else '当前内容窗口')}",
            f"尺寸：{observation.width}x{observation.height}",
            f"截图：{observation.screenshot_rel}",
        ]
        if len(records) > 1:
            detail_lines.append(f"连续采样：{len(records)} 帧，间隔约 {sample_interval_ms}ms，用于理解视频画面变化。")
        body = "\n".join(detail_lines)
        body = f"{body}\n\n{ocr_result.detail_text()}"
        body = f"{body}\n{region_summary}"
        if len(records) > 1:
            body = f"{body}\n\n连续帧文本线索：\n{_sequence_body(records)}"
        if transcript.segments:
            body = f"{body}\n\n实时转写（{transcript.source}）：\n{_transcript_body(transcript)}"
        card_summary = self._summary(observation)
        if summary_text:
            body = f"视觉摘要：{summary_text}\n\n{body}"
            card_summary = summary_text
        elif self.summarizer is None:
            body = f"{body}\n\n视觉摘要：未配置视觉模型，截图已保存。"
            card_summary = "截图已保存；配置视觉模型后可以生成画面摘要。"
        elif summary_error:
            body = f"{body}\n\n视觉摘要：生成失败，截图已保存。"
            card_summary = "截图已保存；视觉摘要暂时没有生成出来。"

        observation_state = {
            **observation.to_agent_state(),
            "ocr": ocr_result.to_agent_state(),
            "ocr_regions": regions_to_agent_state(ocr_regions),
        }
        watch_frames = [
            _record_to_watch_state(record, summary_text if len(records) > 1 and index == len(records) - 1 else "")
            for index, record in enumerate(records)
        ]
        agent_state: dict = {
            "tool": self.name,
            "computer_observation": computer_observation.to_agent_state(),
            "first_computer_observation": first_record["computer_observation"].to_agent_state(),
            "observation": observation_state,
            "watch_frames": watch_frames,
            "temporal_observation": len(records) > 1,
            "sequence_summary": summary_text if len(records) > 1 else "",
            "transcript": transcript.to_agent_state(),
            "model_status": model_status,
            "artifacts": [record["observation"].screenshot_rel for record in records],
        }
        if summary_text:
            agent_state["vision_summary"] = summary_text
        if summary_error:
            agent_state["vision_summary_error"] = summary_error

        voice = self._voice_for(observation, summary_text, summary_error)

        return ToolResult(
            ok=True,
            agent_state=agent_state,
            display_card=DisplayCard(
                "画面观察",
                card_summary,
                body,
                status="success",
                artifacts=[record["observation"].screenshot_rel for record in records],
            ),
            voice_line=voice,
        )

    def _capture_records(self, target: str, query: str, sample_count: int, sample_interval_ms: int) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        count = max(1, min(4, int(sample_count or 1)))
        for index in range(count):
            if index > 0:
                time.sleep(max(0, sample_interval_ms) / 1000.0)
            computer_observation = self.computer_backend.observe(target=target, query=query)
            observation = computer_observation.to_vision()
            ocr_result = self._run_ocr(observation)
            ocr_regions = group_ocr_regions(ocr_result, observation.width, observation.height)
            records.append(
                {
                    "computer_observation": computer_observation,
                    "observation": observation,
                    "ocr_result": ocr_result,
                    "ocr_regions": ocr_regions,
                }
            )
        return records

    def _summarize_records(self, records: list[dict[str, Any]], query: str, transcript: TranscriptResult):
        observations = [record["observation"] for record in records]
        if len(records) > 1:
            sequence_summarizer = getattr(self.summarizer, "summarize_sequence", None)
            if callable(sequence_summarizer):
                ocr_lines = _ocr_lines_for_sequence(records)
                transcript_text = transcript.compact_text()
                if transcript_text:
                    ocr_lines.append(f"实时转写：{transcript_text}")
                return sequence_summarizer(observations, query, ocr_lines)
        return self.summarizer.summarize(observations[-1], query)

    def _transcribe_records(self, records: list[dict[str, Any]], request: ToolRequest, sample_interval_ms: int) -> TranscriptResult:
        source = str(request.arguments.get("transcript_source") or "auto").strip().casefold()
        query = str(request.arguments.get("query") or "")
        wants_transcript = bool(request.arguments.get("transcribe")) or _looks_like_transcript_watch(query) or _looks_like_video_watch(query)
        if source in {"system_audio", "audio"} and self.audio_transcriber is not None:
            seconds = max(1.0, min(10.0, (len(records) * max(300, sample_interval_ms)) / 1000.0))
            audio_result = self.audio_transcriber.transcribe(seconds)
            if audio_result.ok:
                return audio_result
            fallback = transcript_from_ocr_records(records, sample_interval_ms)
            if fallback.ok:
                return TranscriptResult(fallback.status, fallback.source, fallback.segments, fallback.summary, audio_result.error)
            return audio_result
        if source in {"system_audio", "audio"}:
            fallback = transcript_from_ocr_records(records, sample_interval_ms)
            if fallback.ok:
                return TranscriptResult(fallback.status, fallback.source, fallback.segments, fallback.summary, "system_audio_unavailable")
            return TranscriptResult("unavailable", "system_audio", [], "系统音频转写不可用。", "system_audio_unavailable")
        if not wants_transcript:
            return TranscriptResult("empty", "ocr_subtitle", [], "未请求视频转写。", "")
        return transcript_from_ocr_records(records, sample_interval_ms)

    def _run_ocr(self, observation: VisionObservation) -> OcrResult:
        return run_ocr_safely(self.ocr, observation.screenshot_path)

    @staticmethod
    def _summary(observation: VisionObservation) -> str:
        target = "全屏" if observation.target == "fullscreen" else "当前窗口"
        title = f"《{observation.title[:28]}》" if observation.title else target
        return f"已截取{title}，尺寸 {observation.width}x{observation.height}。"

    @staticmethod
    def _target_from_request(request: ToolRequest) -> str:
        target = str(request.arguments.get("target") or "").strip()
        query = str(request.arguments.get("query") or "")
        if target:
            return target
        if any(token in query for token in ("全屏", "整个屏幕", "全桌面", "fullscreen")):
            return "fullscreen"
        return "active_window"

    @staticmethod
    def _sample_count_from_request(request: ToolRequest) -> int:
        raw = request.arguments.get("sample_count")
        if raw is not None:
            try:
                return max(1, min(8, int(raw)))
            except Exception:
                return 1
        query = str(request.arguments.get("query") or "")
        if _looks_like_transcript_watch(query):
            return 8
        if _looks_like_video_watch(query):
            return 3
        return 1

    @staticmethod
    def _sample_interval_ms_from_request(request: ToolRequest) -> int:
        raw = request.arguments.get("sample_interval_ms")
        if raw is not None:
            try:
                return max(0, min(2500, int(raw)))
            except Exception:
                return 800
        return 900

    @staticmethod
    def _voice_for(observation: VisionObservation, summary_text: str, summary_error: str = "") -> VoiceLine:
        if summary_text:
            return safe_voice_line("我看到了主要内容，摘要已经放进卡片里。", sprite="5")
        if summary_error:
            return safe_voice_line("我截到画面了，摘要暂时没生成出来。", sprite="4")
        return safe_voice_line("我截到画面了，需要配置视觉模型才能总结内容。", sprite="4")


def _looks_like_video_watch(query: str) -> bool:
    value = query or ""
    return any(
        token in value
        for token in (
            "视频",
            "播放",
            "正在播放",
            "弹幕",
            "字幕",
            "电影",
            "剧情",
            "这一段",
            "这段",
            "在讲什么",
            "讲什么",
            "讲了什么",
            "讲到哪",
            "说了什么",
            "看懂",
            "陪我看这个",
        )
    )


def _looks_like_transcript_watch(query: str) -> bool:
    value = query or ""
    return any(token in value for token in ("实时转写", "转写", "字幕源", "系统音频", "听一下", "听懂", "视频字幕", "字幕转写"))


def _ocr_lines_for_sequence(records: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for record in records:
        ocr = record["ocr_result"]
        blocks = getattr(ocr, "text_blocks", []) or []
        snippets = []
        for block in blocks[:8]:
            text = str(getattr(block, "text", "") or "").strip()
            if text and text not in snippets:
                snippets.append(text[:80])
        if snippets:
            rows.append(" / ".join(snippets[:6]))
        elif getattr(ocr, "summary", ""):
            rows.append(str(ocr.summary)[:120])
    return rows


def _sequence_body(records: list[dict[str, Any]]) -> str:
    rows = []
    for index, record in enumerate(records, start=1):
        observation = record["observation"]
        ocr_line = _ocr_lines_for_sequence([record])
        suffix = f"；文字：{ocr_line[0]}" if ocr_line else ""
        rows.append(f"{index}. {observation.title or '当前画面'} - {observation.screenshot_rel}{suffix}")
    return "\n".join(rows)


def _transcript_body(transcript: TranscriptResult) -> str:
    rows = []
    for index, segment in enumerate(transcript.segments[:8], start=1):
        rows.append(f"{index}. {segment.text[:160]}")
    return "\n".join(rows) if rows else transcript.summary


def _record_to_watch_state(record: dict[str, Any], sequence_summary: str = "") -> dict[str, Any]:
    observation = record["observation"]
    ocr_result = record["ocr_result"]
    ocr_regions = record["ocr_regions"]
    return {
        **observation.to_agent_state(),
        "ocr": ocr_result.to_agent_state(),
        "ocr_regions": regions_to_agent_state(ocr_regions),
        "sequence_summary": sequence_summary,
    }
