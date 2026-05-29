from __future__ import annotations

import base64
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from agent_companion.core.vision.schemas import VisionObservation


@dataclass(frozen=True)
class VisionSummary:
    text: str
    model: str = ""
    error: str = ""


class VisionSummarizer(Protocol):
    def summarize(self, observation: VisionObservation, query: str = "") -> VisionSummary:
        ...


class MockSummarizer:
    def summarize(self, observation: VisionObservation, query: str = "") -> VisionSummary:
        target = "全屏" if observation.target == "fullscreen" else "当前窗口"
        title = f"《{observation.title[:28]}》" if observation.title else target
        return VisionSummary(text=f"画面摘要：{title}，尺寸 {observation.width}x{observation.height}。", model="mock")

    def summarize_sequence(self, observations: Sequence[VisionObservation], query: str = "", ocr_texts: Sequence[str] | None = None) -> VisionSummary:
        latest = observations[-1] if observations else None
        if latest is None:
            return VisionSummary(text="", model="mock", error="empty sequence")
        title = f"《{latest.title[:28]}》" if latest.title else "当前视频画面"
        ocr_hint = "；可见文字：" + " / ".join(text for text in (ocr_texts or []) if text)[:160] if ocr_texts else ""
        return VisionSummary(text=f"连续画面摘要：采样了 {len(observations)} 帧，{title} 正在播放的视频画面有变化{ocr_hint}。", model="mock")


class OpenAIVisionSummarizer:
    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def summarize(self, observation: VisionObservation, query: str = "") -> VisionSummary:
        screenshot_path = observation.screenshot_path
        if not screenshot_path.is_file():
            return VisionSummary(text="", error=f"screenshot not found: {screenshot_path}")

        image_bytes = screenshot_path.read_bytes()
        b64 = base64.b64encode(image_bytes).decode("ascii")

        prompt = self._primary_prompt(query)
        result = self._request_summary(b64, prompt)
        if result.text or result.error:
            return result
        fallback = self._request_summary(b64, self._fallback_prompt(query))
        if fallback.text:
            return fallback
        return result

    def summarize_sequence(self, observations: Sequence[VisionObservation], query: str = "", ocr_texts: Sequence[str] | None = None) -> VisionSummary:
        rows = [observation for observation in observations if observation.screenshot_path.is_file()]
        if not rows:
            return VisionSummary(text="", error="no screenshots for sequence")
        images: list[str] = []
        for observation in rows[:4]:
            images.append(base64.b64encode(observation.screenshot_path.read_bytes()).decode("ascii"))
        prompt = self._sequence_prompt(query, list(ocr_texts or []), len(rows))
        return self._request_summary(images, prompt, max_tokens=520)

    def _primary_prompt(self, query: str = "") -> str:
        prompt = (
            "你是一个视觉摘要助手。请仔细观察这张截图，基于可见内容回答。\n"
            "规则：\n"
            "- 直接以截图为准，不要依赖窗口标题或 OCR 结论。\n"
            "- 如果是网页、视频或游戏画面，先说明页面/画面的可见主题，再列出关键按钮、卡片、标题或状态。\n"
            "- 如果用户要求定位目标，说明目标最可能位于画面的哪个区域，例如左上、右下、顶部导航、视频卡片下方。\n"
            "- 如果用户有问题，结合截图内容回答，不要说“看不到”除非截图确实没有相关内容。\n"
            "- 不要输出 JSON、路径、命令、token、日志、英文 ID。\n"
            "- 不要猜测截图中不可见的信息。\n"
        )
        if query:
            prompt += f"\n用户问题：{query}"
        return prompt

    @staticmethod
    def _fallback_prompt(query: str = "") -> str:
        prompt = "请用中文概括这张截图中可见的页面或画面内容，说明主要主题和关键可见区域。不要输出 JSON。"
        if query:
            prompt += f"\n用户问题：{query}"
        return prompt

    @staticmethod
    def _sequence_prompt(query: str = "", ocr_texts: list[str] | None = None, frame_count: int = 0) -> str:
        prompt = (
            f"你是视频陪看视觉助手。下面是同一窗口连续采样的 {frame_count or '多'} 帧截图。\n"
            "请根据连续画面判断正在播放的视频大概关于什么、画面中发生了什么、有哪些可见字幕/弹幕/角色/动作变化。\n"
            "规则：\n"
            "- 不要只复述网页标题，必须优先描述视频画面本身。\n"
            "- 如果画面信息不足，要明确说目前只能看到哪些有限线索。\n"
            "- 如果 OCR/弹幕/字幕文本可用，可以结合它们，但不要把乱码当事实。\n"
            "- 用 2-4 句中文，适合用户继续追问；不要输出 JSON、路径、截图名、工具名、日志或 token。\n"
        )
        snippets = [text for text in (ocr_texts or []) if text][:8]
        if snippets:
            prompt += "\n可见 OCR/字幕/弹幕片段：" + " / ".join(snippets)
        if query:
            prompt += f"\n用户问题：{query}"
        return prompt

    def _request_summary(self, b64: str | list[str], prompt: str, max_tokens: int = 300) -> VisionSummary:
        user_content: list[dict] = []
        user_content.append({"type": "text", "text": prompt})
        images = b64 if isinstance(b64, list) else [b64]
        for image in images[:4]:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image}"},
            })

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": user_content}],
            "max_tokens": max_tokens,
        }

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        body = json.dumps(payload).encode("utf-8")

        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            message = data["choices"][0]["message"]
            text = _content_text(message.get("content")).strip()
            return VisionSummary(text=text, model=data.get("model", self.model))
        except Exception as exc:
            return VisionSummary(text="", error=f"{type(exc).__name__}: {exc}")


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""
