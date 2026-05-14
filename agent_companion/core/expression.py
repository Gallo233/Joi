from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_companion.core.character import CharacterHarness
from agent_companion.core.schemas import AgentEvent, EventType, VoiceLine
from agent_companion.core.voice import safe_voice_line


class ExpressionEngine:
    """Turns internal events into character-safe user-facing speech."""

    def __init__(self, workspace: Path, character: CharacterHarness) -> None:
        self.workspace = workspace
        self.character = character
        self._config = self._load_config()
        self._client: Any | None = None

    def express(self, event: AgentEvent, user_text: str = "") -> AgentEvent:
        if event.type == EventType.USER_MESSAGE:
            return event
        fallback = event.voice_line
        payload = self._llm_expression(event, user_text)
        if payload is None:
            return replace(
                event,
                voice_line=safe_voice_line(fallback.text, emotion=fallback.emotion, sprite=fallback.sprite),
            )

        voice_text = str(payload.get("voice_text") or fallback.text).strip()
        emotion = str(payload.get("emotion") or fallback.emotion or "neutral")
        sprite = str(payload.get("sprite") or fallback.sprite or "1")
        return replace(event, voice_line=safe_voice_line(voice_text, emotion=emotion, sprite=sprite))

    def _llm_expression(self, event: AgentEvent, user_text: str) -> dict[str, Any] | None:
        if os.environ.get("AGENT_COMPANION_DISABLE_LLM") == "1":
            return None
        config = self._config
        if event.agent_state.get("tool") == "companion.chat":
            return None
        if config is None or config.llm.use_mock or not config.llm.is_configured:
            return None
        if event.type not in {
            EventType.APPROVAL_REQUIRED,
            EventType.TOOL_STARTED,
            EventType.TOOL_COMPLETED,
            EventType.TOOL_FAILED,
            EventType.TASK_COMPLETED,
            EventType.TASK_FAILED,
        }:
            return None

        try:
            from openai import OpenAI
        except Exception:
            return None

        try:
            if self._client is None:
                self._client = OpenAI(api_key=config.llm.api_key, base_url=config.llm.base_url)
            character = config.primary_character if config.characters else None
            character_name = character.name if character else self.character.name
            persona = character.setting if character else self.character.persona
            voice_lang = character.voice_text_lang(config.tts.text_lang) if character else self.character.voice.get("default_lang", "zh")
            prompt = {
                "event_type": event.type.value,
                "title": event.display_card.title,
                "summary": event.display_card.summary,
                "status": event.display_card.status,
                "user_text": user_text,
                "voice_lang": voice_lang,
                "fallback_voice": event.voice_line.text,
            }
            response = self._client.chat.completions.create(
                model=config.llm.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"你是{character_name}的表达层，只负责把 Agent 内部事件改写成角色自然短句。\n"
                            f"角色设定：{persona[:1600]}\n"
                            "规则：只输出 JSON；格式为 {\"voice_text\":\"...\",\"emotion\":\"neutral|happy|worried|serious\",\"sprite\":\"1\"}。"
                            "voice_text 必须短，适合朗读。禁止包含 JSON、task id、命令、路径、token、日志、退出码。"
                            "不要夸大工具结果：如果卡片只说已启动或已接收任务，不要说已经通关、完成日常或修好了。"
                            "如果 voice_lang 不是中文，voice_text 用该语言自然表达；不要解释。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                temperature=min(max(config.llm.temperature, 0.2), 0.9),
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def _load_config(self) -> Any | None:
        config_path = self.workspace / "config.yaml"
        if not config_path.is_file():
            return None
        try:
            from agent_companion.core.config import load_app_config

            return load_app_config(config_path)
        except Exception:
            return None
