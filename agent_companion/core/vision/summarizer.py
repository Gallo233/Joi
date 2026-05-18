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

        user_content: list[dict] = []
        prompt = (
            "你是一个视觉摘要助手。请仔细观察这张截图，基于可见内容回答。\n"
            "规则：\n"
            "- 用简短中文描述截图中可见的主要内容。\n"
            "- 如果用户有问题，结合截图内容回答。\n"
            "- 不要输出 JSON、路径、命令、token、日志、英文 ID。\n"
            "- 不要猜测截图中不可见的信息。\n"
        )
        if query:
            prompt += f"\n用户问题：{query}"
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
            text = data["choices"][0]["message"]["content"].strip()
            return VisionSummary(text=text, model=data.get("model", self.model))
        except Exception as exc:
            return VisionSummary(text="", error=f"{type(exc).__name__}: {exc}")
