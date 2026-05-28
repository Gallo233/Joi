from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import normalize_emotion, safe_voice_line, sprite_for_emotion


class CompanionChatTool(ToolAdapter):
    name = "companion.chat"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self._config: Any | None = self._load_config()
        self._client: Any | None = None

    def run(self, request: ToolRequest) -> ToolResult:
        text = str(request.arguments.get("text") or "").strip()
        reply, voice_text, emotion, sprite = self._reply(text)
        voice_line = safe_voice_line(voice_text or reply, emotion=emotion, sprite=sprite)
        return ToolResult(
            ok=True,
            agent_state={
                "tool": self.name,
                "reply": reply,
                "expression_sync": {
                    "emotion": voice_line.emotion,
                    "sprite": voice_line.sprite,
                    "voice_style": voice_line.emotion,
                },
            },
            display_card=DisplayCard("对话", reply, status="success"),
            voice_line=voice_line,
        )

    def _reply(self, text: str) -> tuple[str, str, str, str]:
        fallback = "我在。你可以直接告诉我要看、要玩，还是要写代码。"
        fallback_emotion = _fallback_chat_emotion(text)
        config = self._config
        if os.environ.get("AGENT_COMPANION_DISABLE_LLM") == "1" or config is None or config.llm.use_mock or not config.llm.is_configured:
            sprite = _sprite_for_character_emotion(config.primary_character if config else None, fallback_emotion)
            return (fallback if not text else f"我听到了：{text}", fallback if not text else f"我听到了。", fallback_emotion, sprite)
        try:
            from openai import OpenAI

            from agent_companion.core.config import ModelRouter

            router = ModelRouter(config.llm)
            endpoint = router.resolve("text")
            if self._client is None:
                self._client = OpenAI(api_key=endpoint.api_key, base_url=endpoint.base_url)
            character = config.primary_character
            voice_lang = character.voice_text_lang(config.tts.text_lang)
            sprite_catalog = _sprite_catalog(character)
            response = self._client.chat.completions.create(
                model=endpoint.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"你是{character.name}，按角色设定和用户自然聊天。\n"
                            f"角色设定：{character.setting[:2200]}\n"
                            "只输出 JSON：{\"reply\":\"给屏幕显示的中文回复\",\"voice_text\":\"<emo: happy>适合配音朗读的短句\",\"emotion\":\"neutral|happy|thinking|alert|worried|serious\",\"sprite\":\"1\"}。"
                            "emotion 必须贴合回复语气；sprite 必须从可用立绘 id 中选择最贴近 emotion 的一个。"
                            f"可用立绘：{sprite_catalog}。\n"
                            f"voice_text 使用 {voice_lang}，可在开头带 <emo: ...>，不要包含 JSON、路径、命令、密钥 token 或日志。"
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
            emotion = normalize_emotion(str(payload.get("emotion") or "") or _fallback_chat_emotion(f"{text} {reply} {voice_text}"))
            sprite = _valid_character_sprite(character, str(payload.get("sprite") or ""), emotion)
            return reply[:600], voice_text[:180], emotion, sprite
        except Exception:
            sprite = _sprite_for_character_emotion(config.primary_character if config else None, fallback_emotion)
            return (fallback if not text else f"我听到了：{text}", fallback if not text else "我听到了。", fallback_emotion, sprite)

    def _load_config(self) -> Any | None:
        config_path = self.workspace / "config.yaml"
        if not config_path.is_file():
            return None
        try:
            from agent_companion.core.config import load_app_config

            return load_app_config(config_path)
        except Exception:
            return None


def _sprite_catalog(character: Any) -> str:
    sprites = getattr(character, "sprites", []) or []
    rows = []
    for sprite in sprites[:28]:
        sprite_id = str(getattr(sprite, "id", "") or "").strip()
        label = str(getattr(sprite, "label", "") or "").strip()
        if sprite_id:
            rows.append(f"{sprite_id}={label or 'default'}")
    return "；".join(rows) or "1=default"


def _fallback_chat_emotion(text: str) -> str:
    value = text or ""
    if any(token in value for token in ("开心", "高兴", "快乐", "兴奋", "喜欢", "太好了", "成就感", "顺利")):
        return "happy"
    if any(token in value for token in ("想", "思考", "为什么", "怎么", "吗", "？", "?", "好奇")):
        return "thinking"
    if any(token in value for token in ("危险", "警告", "注意", "不能", "失败", "报错", "崩", "坏了")):
        return "alert"
    if any(token in value for token in ("担心", "不安", "难过", "沮丧", "抱歉")):
        return "worried"
    if any(token in value for token in ("认真", "严肃", "专注", "确认")):
        return "serious"
    return "neutral"


EMOTION_LABEL_HINTS = {
    "happy": ("开心", "微笑", "温柔", "兴奋", "激动", "俏皮", "可爱", "高兴"),
    "thinking": ("沉思", "思考", "好奇", "疑惑", "查看", "歪头"),
    "alert": ("警告", "阻止", "制止", "吃惊", "生气", "不满"),
    "worried": ("担忧", "担心", "不安", "难过", "沮丧", "悲伤", "自闭"),
    "serious": ("严肃", "指点", "解释", "果断", "陈述", "冷漠"),
    "neutral": ("中性", "平静", "普通", "默认"),
}


def _sprite_for_character_emotion(character: Any, emotion: str) -> str:
    normalized = normalize_emotion(emotion)
    sprites = getattr(character, "sprites", []) or []
    hints = EMOTION_LABEL_HINTS.get(normalized, ())
    for hint in hints:
        for sprite in sprites:
            label = str(getattr(sprite, "label", "") or "")
            sprite_id = str(getattr(sprite, "id", "") or "").strip()
            if sprite_id and hint in label:
                return sprite_id
    mapped = sprite_for_emotion(normalized)
    if any(str(getattr(sprite, "id", "") or "") == mapped for sprite in sprites):
        return mapped
    if sprites:
        return str(getattr(sprites[0], "id", "") or "1")
    return mapped


def _valid_character_sprite(character: Any, sprite: str, emotion: str) -> str:
    value = str(sprite or "").strip()
    sprites = getattr(character, "sprites", []) or []
    if value and any(str(getattr(item, "id", "") or "") == value for item in sprites):
        return value
    return _sprite_for_character_emotion(character, emotion)
