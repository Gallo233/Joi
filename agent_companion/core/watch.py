from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
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
            "created_at": self.created_at,
        }


class WatchSession:
    def __init__(self, limit: int = 5) -> None:
        self.limit = max(1, limit)
        self._frames: list[WatchFrame] = []

    def add(self, frame: WatchFrame) -> None:
        if not frame.summary and not frame.artifact:
            return
        self._frames.append(frame)
        self._frames = self._frames[-self.limit :]

    def recent(self, limit: int = 3) -> list[WatchFrame]:
        return list(reversed(self._frames[-max(1, limit) :]))

    def has_context(self) -> bool:
        return bool(self._frames)


def answer_from_recent_frames(question: str, frames: list[WatchFrame]) -> tuple[str, str]:
    if not frames:
        return "我还没有最近的画面上下文。先让我看一下当前窗口吧。", ""
    latest = frames[0]
    title = f"《{latest.title}》" if latest.title else "刚才的画面"
    if latest.model_status == "unconfigured":
        return f"{title}的截图已经保存了，但还没有配置视觉模型，所以我现在只能确认画面已记录。", "vision_unconfigured"
    if latest.model_status == "error":
        return f"{title}的截图已经保存了，不过视觉摘要暂时没生成出来。", "vision_error"
    ocr_line = _ocr_line(latest)
    question_hint = question or ""
    region_answer = _region_answer(question_hint, latest)
    if region_answer:
        return region_answer, "vision_context"
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

    def answer(self, question: str, frames: list[WatchFrame]) -> tuple[str, str]:
        fallback, status = answer_from_recent_frames(question, frames)
        self.last_used_model = False
        if not frames or os.environ.get("AGENT_COMPANION_DISABLE_LLM") == "1":
            return fallback, status
        config = self._config
        if config is None or config.llm.use_mock or not (config.llm.is_expression_configured or config.llm.is_configured):
            return fallback, status
        try:
            from openai import OpenAI

            from agent_companion.core.config import ModelRouter

            router = ModelRouter(config.llm)
            endpoint = router.resolve("expression" if config.llm.is_expression_configured else "text")
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
                    "user_question": frame.user_question,
                    "model_status": frame.model_status,
                    "age_seconds": int(time.time() - frame.created_at),
                }
                for frame in frames[:3]
            ]
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
            payload = json.loads(response.choices[0].message.content or "{}")
            answer = str(payload.get("answer") or "").strip()
            if not answer:
                return fallback, status
            self.last_used_model = True
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
    base = frame.summary.strip()
    ocr = _ocr_line(frame)
    if base and ocr:
        return f"{base}；可见文字：{ocr}"
    return base or f"可见文字：{ocr}"
