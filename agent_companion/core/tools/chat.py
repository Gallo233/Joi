from __future__ import annotations

import json
import os
from pathlib import Path
import time
from dataclasses import dataclass
from typing import Any

from agent_companion.core.config import load_workspace_config
from agent_companion.core.character_packages import CharacterPackageManager
from agent_companion.core.memory_candidates import chat_memory_candidate
from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import normalize_emotion, safe_voice_line, sprite_for_emotion


class CompanionChatTool(ToolAdapter):
    name = "companion.chat"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self._config: Any | None = self._load_config()
        self._characters = CharacterPackageManager(self.workspace)
        self._client: Any | None = None

    def run(self, request: ToolRequest) -> ToolResult:
        text = str(request.arguments.get("text") or "").strip()
        memory_context = _memory_context(request.arguments.get("memory_context"))
        chat = self._reply(text, memory_context)
        voice_line = safe_voice_line(chat.voice_text or chat.reply, emotion=chat.emotion, sprite=chat.sprite)
        agent_state: dict[str, Any] = {
            "tool": self.name,
            "reply": chat.reply,
            "memory_context": memory_context,
            "expression_sync": {
                "emotion": voice_line.emotion,
                "sprite": voice_line.sprite,
                "voice_style": voice_line.emotion,
            },
        }
        memory_profile = _memory_profile(memory_context)
        if memory_profile:
            agent_state["memory_profile"] = memory_profile
        memory_candidate = chat_memory_candidate(text)
        if memory_candidate:
            agent_state["memory_candidate"] = memory_candidate
        if chat.model_usage:
            agent_state["model_usage"] = chat.model_usage
        if chat.error:
            agent_state["model_error"] = chat.error
        return ToolResult(
            ok=not chat.error,
            agent_state=agent_state,
            display_card=DisplayCard("对话", chat.reply, status="failed" if chat.error else "success"),
            voice_line=voice_line,
        )

    def _reply(self, text: str, memory_context: list[dict[str, str]] | None = None) -> "ChatReply":
        fallback = "我在。你可以直接告诉我要看、要玩，还是要写代码。"
        fallback_emotion = _fallback_chat_emotion(text)
        config = self._config
        if os.environ.get("AGENT_COMPANION_DISABLE_LLM") == "1":
            memory_reply = _fallback_memory_reply(text, memory_context or [])
            if memory_reply:
                sprite = _sprite_for_character_emotion(config.primary_character if config else None, "thinking")
                return ChatReply(memory_reply, "我记得这一点。", "thinking", sprite)
            sprite = _sprite_for_character_emotion(config.primary_character if config else None, "worried")
            return ChatReply("模型调用已被运行环境关闭。", "模型调用现在是关闭的。", "worried", sprite, error="model_disabled")
        if config is None or not config.llm.is_configured:
            memory_reply = _fallback_memory_reply(text, memory_context or [])
            if memory_reply:
                sprite = _sprite_for_character_emotion(config.primary_character if config else None, "thinking")
                return ChatReply(memory_reply, "我记得这一点。", "thinking", sprite)
            sprite = _sprite_for_character_emotion(config.primary_character if config else None, "worried")
            return ChatReply(
                "BYOK 还没有连接。请到“设置 → 执行模式 → BYOK”完成三步连接，或切回 Codex CLI。",
                "BYOK 还没有连接，请先完成模型设置。",
                "worried",
                sprite,
                error="model_unconfigured",
            )
        if config.llm.use_mock:
            sprite = _sprite_for_character_emotion(config.primary_character, fallback_emotion)
            memory_reply = _fallback_memory_reply(text, memory_context or [])
            if memory_reply:
                return ChatReply(memory_reply, "我记得这一点。", "thinking", sprite, model_usage={"mock": True})
            return ChatReply(fallback, fallback, fallback_emotion, sprite, model_usage={"mock": True})
        try:
            from openai import OpenAI

            from agent_companion.core.config import ModelRouter

            router = ModelRouter(config.llm)
            endpoint = router.resolve("fast")
            if self._client is None:
                self._client = OpenAI(api_key=endpoint.api_key or "ollama", base_url=endpoint.base_url, timeout=30.0)
            character = config.primary_character
            voice_lang = character.voice_text_lang(config.tts.text_lang)
            sprite_catalog = _sprite_catalog(character)
            memory_prompt = _memory_prompt(memory_context or [])
            lore_rows = self._characters.lore_context(text)
            lore_prompt = ""
            if lore_rows:
                lore_prompt = "本轮触发的角色知识：\n" + "\n".join(f"- {row['content']}" for row in lore_rows) + "\n"
            system_prompt = (
                f"你是{character.name}，按角色设定和用户自然聊天。\n"
                f"角色设定：{character.setting[:2200]}\n"
                f"{memory_prompt}"
                f"{lore_prompt}"
                "回复要像轻松的桌面陪伴对话：先直接回应用户，默认用 2 至 4 个短句。"
                "只有确实存在三个以上并列事项时才使用列表，避免复述用户原话、执行日志和无关背景。"
                "能一句说清就不要扩写；必须补充信息时最多问一个问题。\n"
                "只输出 JSON：{\"reply\":\"给屏幕显示的中文回复\",\"voice_text\":\"<emo: happy>适合配音朗读的短句\",\"emotion\":\"neutral|happy|thinking|alert|worried|serious\",\"sprite\":\"1\"}。"
                "emotion 必须贴合回复语气；sprite 必须从可用立绘 id 中选择最贴近 emotion 的一个。"
                f"可用立绘：{sprite_catalog}。\n"
                f"voice_text 使用 {voice_lang}，可在开头带 <emo: ...>，不要包含 JSON、路径、命令、密钥 token 或日志。"
            )
            started = time.perf_counter()
            if endpoint.provider == "openai":
                response = self._client.responses.create(
                    model=endpoint.model,
                    instructions=system_prompt,
                    input=text or "你好",
                    max_output_tokens=600,
                    store=False,
                )
                content = str(response.output_text or "").strip()
            else:
                response = self._client.chat.completions.create(
                    model=endpoint.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text or "你好"},
                    ],
                    temperature=config.llm.temperature,
                )
                content = str(response.choices[0].message.content or "").strip()
            latency_ms = (time.perf_counter() - started) * 1000
            try:
                payload = json.loads(content or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {"reply": content}
            if not isinstance(payload, dict):
                payload = {"reply": content}
            reply = str(payload.get("reply") or content or fallback).strip()
            voice_text = str(payload.get("voice_text") or reply).strip()
            emotion = normalize_emotion(str(payload.get("emotion") or "") or _fallback_chat_emotion(f"{text} {reply} {voice_text}"))
            sprite = _valid_character_sprite(character, str(payload.get("sprite") or ""), emotion)
            return ChatReply(reply[:600], voice_text[:180], emotion, sprite, endpoint.to_agent_state(latency_ms=latency_ms))
        except Exception as exc:
            memory_reply = _fallback_memory_reply(text, memory_context or [])
            if memory_reply:
                sprite = _sprite_for_character_emotion(config.primary_character if config else None, "thinking")
                return ChatReply(memory_reply, "我记得这一点。", "thinking", sprite)
            error = _chat_error_code(exc)
            message = _chat_error_message(error)
            sprite = _sprite_for_character_emotion(config.primary_character if config else None, "worried")
            return ChatReply(message, message, "worried", sprite, error=error)

    def _load_config(self) -> Any | None:
        return load_workspace_config(self.workspace)


@dataclass(frozen=True)
class ChatReply:
    reply: str
    voice_text: str
    emotion: str
    sprite: str
    model_usage: dict[str, Any] | None = None
    error: str = ""


def _chat_error_code(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.casefold()
    if status in {401, 403} or "authentication" in name or "permissiondenied" in name:
        return "authentication_failed"
    if status == 404:
        return "model_or_endpoint_not_found"
    if status == 429 or "ratelimit" in name:
        return "rate_limited"
    if "timeout" in name:
        return "model_timeout"
    if "connection" in name:
        return "endpoint_unreachable"
    return "model_request_failed"


def _chat_error_message(error: str) -> str:
    messages = {
        "authentication_failed": "BYOK 密钥未通过验证，请到设置里重新连接。",
        "model_or_endpoint_not_found": "没有找到配置的模型或接口，请检查 BYOK 的模型名与端点。",
        "rate_limited": "模型供应商暂时限流或额度不足，请稍后再试并检查用量。",
        "model_timeout": "模型响应超时了，请检查网络或换用更快的模型。",
        "endpoint_unreachable": "目前连接不到 BYOK 接口，请检查端点和网络。",
        "model_request_failed": "BYOK 请求没有成功，请到设置里运行连接测试。",
    }
    return messages.get(error, messages["model_request_failed"])


def _sprite_catalog(character: Any) -> str:
    sprites = getattr(character, "sprites", []) or []
    rows = []
    for sprite in sprites[:28]:
        sprite_id = str(getattr(sprite, "id", "") or "").strip()
        label = str(getattr(sprite, "label", "") or "").strip()
        if sprite_id:
            rows.append(f"{sprite_id}={label or 'default'}")
    return "；".join(rows) or "1=default"


def _memory_context(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for item in value[:8]:
        if isinstance(item, str):
            text = item.strip()
            kind = "note"
            source = "memory"
        elif isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            kind = str(item.get("kind") or "note").strip()
            source = str(item.get("source") or "memory").strip()
        else:
            continue
        if text and _safe_memory_text(text):
            rows.append({"kind": kind[:40] or "note", "text": text[:400], "source": source[:40] or "memory"})
    return rows


def _memory_prompt(memory_context: list[dict[str, str]]) -> str:
    if not memory_context:
        return ""
    profile_rows = [row for row in memory_context[:3] if row.get("source") == "memory_profile" and row.get("text")]
    fact_rows = [row for row in memory_context[:8] if row.get("source") != "memory_profile" and row.get("text")]
    lines = ["已确认长期记忆，只作为用户偏好和背景使用，不要透露为系统日志或截图："]
    for row in profile_rows:
        lines.append(f"- 长期画像：{row.get('text', '')[:260]}")
    for row in fact_rows[:7]:
        lines.append(f"- {row.get('text', '')[:240]}")
    return "\n".join(lines) + "\n"


def _fallback_memory_reply(text: str, memory_context: list[dict[str, str]]) -> str:
    if not memory_context:
        return ""
    value = text or ""
    if not any(token in value for token in ("喜欢", "偏好", "习惯", "记得", "知道我", "了解我")):
        return ""
    facts = [
        row["text"].replace("用户画像：", "", 1)
        for row in memory_context[:4]
        if row.get("text")
    ]
    if not facts:
        return ""
    return "我记得：" + "；".join(facts)


def _memory_profile(memory_context: list[dict[str, str]]) -> dict[str, str]:
    for row in memory_context:
        if row.get("source") == "memory_profile" and row.get("text"):
            return {"kind": row.get("kind", "profile"), "text": row["text"]}
    return {}


def _safe_memory_text(text: str) -> bool:
    forbidden = (
        "sk-",
        "api_key",
        "token",
        "secret",
        "password",
        "http://",
        "https://",
        "data/agent_companion/",
        "screenshot",
        "traceback",
        "stderr",
        "stdout",
        "approval-",
        "task-",
        "selection-",
        "codex-",
    )
    lowered = text.casefold()
    if any(item in lowered for item in forbidden):
        return False
    return not any(item in text for item in ("C:\\", "/Users/", "/home/", ".png", ".jpg", ".json", ".log", ".yaml", ".yml"))


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
