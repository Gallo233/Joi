from __future__ import annotations

import base64
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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

    def _request_summary(self, b64: str, prompt: str) -> VisionSummary:
        user_content: list[dict] = []
        user_content.append({"type": "text", "text": prompt})
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": user_content}],
            "max_tokens": 300,
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
