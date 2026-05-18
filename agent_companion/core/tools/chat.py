from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class CompanionChatTool(ToolAdapter):
    name = "companion.chat"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self._config: Any | None = self._load_config()
        self._client: Any | None = None

    def run(self, request: ToolRequest) -> ToolResult:
        text = str(request.arguments.get("text") or "").strip()
        reply, voice_text, sprite = self._reply(text)
        return ToolResult(
            ok=True,
            agent_state={"tool": self.name, "reply": reply},
            display_card=DisplayCard("对话", reply, status="success"),
            voice_line=safe_voice_line(voice_text or reply, sprite=sprite),
        )

    def _reply(self, text: str) -> tuple[str, str, str]:
        fallback = "我在。你可以直接告诉我要看、要玩，还是要写代码。"
        config = self._config
        if os.environ.get("AGENT_COMPANION_DISABLE_LLM") == "1" or config is None or config.llm.use_mock or not config.llm.is_configured:
            return (fallback if not text else f"我听到了：{text}", fallback if not text else f"我听到了。", "1")
        try:
            from openai import OpenAI

            from agent_companion.core.config import ModelRouter

            router = ModelRouter(config.llm)
            endpoint = router.resolve("text")
            if self._client is None:
                self._client = OpenAI(api_key=endpoint.api_key, base_url=endpoint.base_url)
            character = config.primary_character
            voice_lang = character.voice_text_lang(config.tts.text_lang)
            response = self._client.chat.completions.create(
                model=endpoint.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"你是{character.name}，按角色设定和用户自然聊天。\n"
                            f"角色设定：{character.setting[:2200]}\n"
                            "只输出 JSON：{\"reply\":\"给屏幕显示的中文回复\",\"voice_text\":\"适合配音朗读的短句\",\"sprite\":\"1\"}。"
                            f"voice_text 使用 {voice_lang}，不要包含 JSON、路径、命令、token 或日志。"
                        ),
                    },
                    {"role": "user", "content": text or "你好"},
                ],
                temperature=config.llm.temperature,
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.choices[0].message.content or "{}")
            reply = str(payload.get("reply") or fallback).strip()
            voice_text = str(payload.get("voice_text") or reply).strip()
            sprite = str(payload.get("sprite") or "1")
            return reply[:600], voice_text[:180], sprite
        except Exception:
            return (fallback if not text else f"我听到了：{text}", fallback if not text else "我听到了。", "1")

    def _load_config(self) -> Any | None:
        config_path = self.workspace / "config.yaml"
        if not config_path.is_file():
            return None
        try:
            from agent_companion.core.config import load_app_config

            return load_app_config(config_path)
        except Exception:
            return None
