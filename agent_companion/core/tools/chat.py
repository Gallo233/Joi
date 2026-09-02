from __future__ import annotations

import json
import os
from pathlib import Path
import time
from dataclasses import dataclass
from typing import Any

from agent_companion.core.config import load_workspace_config
from agent_companion.core.character_packages import CharacterPackageManager
from agent_companion.core.language_policy import (
    DisplayLanguagePolicy,
    chat_language_policy,
    language_mismatch_fallback,
    obvious_language_mismatch,
    obvious_voice_language_mismatch,
    voice_language_label,
)
from agent_companion.core.memory_candidates import chat_memory_candidate
from agent_companion.core.model_call import CallBudget
from agent_companion.core.provider_client import chat_completion
from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import normalize_emotion, normalize_voice_delivery, safe_voice_line, sprite_for_emotion


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
        voice_line = safe_voice_line(
            chat.voice_text or chat.reply,
            emotion=chat.emotion,
            sprite=chat.sprite,
            delivery=chat.delivery,
        )
        agent_state: dict[str, Any] = {
            "tool": self.name,
            "reply": chat.reply,
            "display_language": chat.display_language,
            "voice_language": chat.voice_language,
            "display_language_repaired": chat.language_repaired,
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
        config = self._config
        chat_language = str(getattr(getattr(config, "language", None), "chat", "") or "")
        display_policy = chat_language_policy(chat_language, text)
        fallback = _fallback_chat_reply(display_policy)
        fallback_emotion = _fallback_chat_emotion(text)
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
            character = config.primary_character
            voice_lang = character.voice_text_lang(config.tts.text_lang)
            spoken_language = voice_language_label(voice_lang)
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
                "本轮输出有两个语言互相独立的通道，不能混用：\n"
                f"1. {display_policy.prompt_instruction}\n"
                f"2. voice_text 是配音专用文本，必须使用用户选择的 {spoken_language}；"
                "它要忠实转述 reply 的意思，可以为自然朗读而缩短，但不能增加 reply 没有的信息。\n"
                "只输出 JSON：{\"reply\":\"按用户本轮输入语言显示的回复\",\"voice_text\":\"按所选配音语言朗读的短句\","
                "\"emotion\":\"neutral|happy|thinking|alert|worried|serious\",\"sprite\":\"1\","
                "\"delivery\":{\"intensity\":0.6,\"pace\":\"measured\",\"energy\":\"soft\","
                "\"pause\":\"reflective\",\"emphasis\":\"keywords\",\"relation\":\"close\"}}。"
                "emotion 必须贴合这句话真正的表达意图，不要习惯性选 neutral：庆祝、鼓励或轻松回应用 happy；"
                "共情、关切或遗憾用 worried；推敲、解释思路用 thinking；提醒注意用 alert；"
                "明确边界或郑重说明用 serious；只有普通问候和无明显态度的事实才用 neutral。"
                "delivery 描述怎么说而不是说什么：intensity 为 0.15 至 0.9；pace 只能是 slow/measured/steady/quick；"
                "energy 只能是 soft/balanced/bright/firm；pause 只能是 light/natural/reflective/deliberate/short/gentle；"
                "emphasis 只能是 light/warm/keywords/urgent/caring；relation 只能是 close/supportive/professional/protective。"
                "delivery 要与 emotion 和句意一致，日常对话不要把 intensity 写满。"
                "sprite 必须从可用立绘 id 中选择最贴近 emotion 的一个。"
                f"可用立绘：{sprite_catalog}。\n"
                f"再次确认：reply 跟随用户输入语言；voice_text 才使用 {spoken_language}（{voice_lang}）。"
                "voice_text 写成一到两个适合自然说出的短句，保留有表演意义的逗号、破折号或省略号；"
                "不要写列表、标题、括号舞台动作或任何标签；不要包含 JSON、路径、命令、密钥 token 或日志。"
            )
            started = time.perf_counter()
            outcome = chat_completion(
                config.llm,
                "fast",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text or "你好"},
                ],
                temperature=config.llm.temperature,
                response_format={"type": "json_object"},
                instructions=system_prompt,
                user_input=text or "你好",
            )
            if not outcome.ok:
                raise ChatProviderError(outcome.error_code or outcome.status)
            content = str(outcome.value or "").strip()
            latency_ms = (time.perf_counter() - started) * 1000
            try:
                payload = json.loads(content or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {"reply": content}
            if not isinstance(payload, dict):
                payload = {"reply": content}
            reply = str(payload.get("reply") or content or fallback).strip()
            voice_text = str(payload.get("voice_text") or reply).strip()
            language_repaired = False
            display_mismatch = obvious_language_mismatch(text, reply, chat_language)
            voice_mismatch = obvious_voice_language_mismatch(voice_lang, voice_text)
            if display_mismatch or voice_mismatch:
                repaired = self._repair_language_channels(
                    text,
                    reply,
                    voice_text,
                    display_policy,
                    spoken_language,
                )
                repaired_reply = str(repaired.get("reply") or "").strip()
                repaired_voice = str(repaired.get("voice_text") or "").strip()
                if display_mismatch and repaired_reply and not obvious_language_mismatch(text, repaired_reply, chat_language):
                    reply = repaired_reply
                    language_repaired = True
                elif display_mismatch:
                    in_language_failure = language_mismatch_fallback(display_policy)
                    if in_language_failure:
                        reply = in_language_failure
                        language_repaired = True
                if voice_mismatch and repaired_voice and not obvious_voice_language_mismatch(voice_lang, repaired_voice):
                    voice_text = repaired_voice
                    language_repaired = True
            emotion = normalize_emotion(str(payload.get("emotion") or "") or _fallback_chat_emotion(f"{text} {reply} {voice_text}"))
            delivery = normalize_voice_delivery(payload.get("delivery"), emotion)
            sprite = _valid_character_sprite(character, str(payload.get("sprite") or ""), emotion)
            usage = outcome.endpoint.to_agent_state(latency_ms=latency_ms) if outcome.endpoint is not None else {"latency_ms": max(0, int(latency_ms))}
            return ChatReply(
                reply[:600],
                voice_text[:180],
                emotion,
                sprite,
                usage,
                delivery=delivery,
                display_language=display_policy.code,
                voice_language=str(voice_lang or ""),
                language_repaired=language_repaired,
            )
        except Exception as exc:
            memory_reply = _fallback_memory_reply(text, memory_context or [])
            if memory_reply:
                sprite = _sprite_for_character_emotion(config.primary_character if config else None, "thinking")
                return ChatReply(memory_reply, "我记得这一点。", "thinking", sprite)
            error = _chat_error_code(exc)
            message = _chat_error_message(error)
            sprite = _sprite_for_character_emotion(config.primary_character if config else None, "worried")
            return ChatReply(message, message, "worried", sprite, error=error)

    def _repair_language_channels(
        self,
        user_text: str,
        reply: str,
        voice_text: str,
        policy: DisplayLanguagePolicy,
        spoken_language: str,
    ) -> dict[str, str]:
        """Correct a clear channel mix-up, with a short exceptional-path call."""

        wanted = f"用户选定的聊天语言（{policy.label}）" if policy.chosen else f"用户本轮输入的同一种自然语言（提示：{policy.label}）"
        system_prompt = (
            "你只修复显示与配音两个语言通道，不回答新问题。"
            f"reply 必须使用{wanted}；"
            f"voice_text 必须用用户选择的配音语言 {spoken_language} 忠实转述 reply。"
            "保持原意和角色语气，不添加信息。"
            "只输出 JSON：{\"reply\":\"修复后的屏幕回复\",\"voice_text\":\"修复后的配音短句\"}。"
        )
        repair_input = json.dumps(
            {"user_message": user_text, "reply": reply, "voice_text": voice_text},
            ensure_ascii=False,
        )
        try:
            outcome = chat_completion(
                self._config.llm,
                "fast",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": repair_input},
                ],
                budget=CallBudget(timeout_ms=8_000, max_fallbacks=0, allow_categories=("text",)),
                temperature=0.2,
                response_format={"type": "json_object"},
                instructions=system_prompt,
                user_input=repair_input,
            )
        except Exception:
            return {}
        if not outcome.ok:
            return {}
        content = str(outcome.value or "").strip()
        try:
            payload = json.loads(content or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            "reply": str(payload.get("reply") or "").strip(),
            "voice_text": str(payload.get("voice_text") or "").strip(),
        }

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
    delivery: dict[str, Any] | None = None
    display_language: str = ""
    voice_language: str = ""
    language_repaired: bool = False


class ChatProviderError(RuntimeError):
    """A model call that did not produce a reply, carrying only its code.

    The provider's own message routinely echoes the endpoint and the key, so it
    is never propagated -- `code` comes from the recorded outcome instead.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code or "model_request_failed"


# Outcome codes from model_call mapped onto the user-facing vocabulary the
# settings UI already explains.
_OUTCOME_ERROR_CODES = {
    "route_not_configured": "model_not_configured",
    "budget_timeout": "model_timeout",
    "all_providers_failed": "model_request_failed",
    "unavailable": "model_not_configured",
    "cancelled": "model_request_cancelled",
    "AuthenticationError": "authentication_failed",
    "PermissionDeniedError": "authentication_failed",
    "NotFoundError": "model_or_endpoint_not_found",
    "RateLimitError": "rate_limited",
    "APITimeoutError": "model_timeout",
    "APIConnectionError": "endpoint_unreachable",
    "guest_token_budget_exceeded": "guest_token_budget_exceeded",
    "guest_budget_unavailable": "guest_budget_unavailable",
}


def _chat_error_code(exc: Exception) -> str:
    if isinstance(exc, ChatProviderError):
        code = exc.code
        if code in _OUTCOME_ERROR_CODES:
            return _OUTCOME_ERROR_CODES[code]
        if code.startswith("category_not_allowed") or code.startswith("unknown_data_category"):
            return "model_request_refused"
        if code == "input_budget_exceeded":
            return "model_input_too_large"
        return "model_request_failed"
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
        "model_not_configured": "还没有配置可用的文本模型，请先在设置里连接 BYOK 或本地端点。",
        "guest_token_budget_exceeded": "这次体验的对话额度已经用完，可以重新开始一段 Joi 会话。",
        "guest_budget_unavailable": "体验额度暂时无法确认，我先停在这里，避免继续产生费用。",
        "model_request_cancelled": "这轮请求已取消。",
        "model_request_refused": "这轮请求包含未获授权发送的数据类型，已经拦下。",
        "model_input_too_large": "这轮请求超出了输入预算，请缩短内容后再试。",
        "model_request_failed": "BYOK 请求没有成功，请到设置里运行连接测试。",
    }
    return messages.get(error, messages["model_request_failed"])


def _fallback_chat_reply(policy: DisplayLanguagePolicy) -> str:
    return {
        "zh": "我在。你可以直接告诉我要看、要玩，还是要写代码。",
        "ja": "ここにいます。見たいもの、遊びたいこと、作りたいものをそのまま教えてください。",
        "ko": "여기 있어요. 보고 싶은 것, 하고 싶은 것, 만들고 싶은 것을 바로 말해 주세요.",
        "en": "I'm here. Tell me what you'd like to watch, play, or build.",
    }.get(policy.code, "I'm here. Tell me what you'd like to do.")


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
