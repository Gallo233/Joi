from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_companion.core.character import CharacterHarness
from agent_companion.core.config import load_workspace_config
from agent_companion.core.event_bus import derive_public_phase
from agent_companion.core.expression_map import ExpressionIntent, expression_state_from_event, resolve_expression
from agent_companion.core.provider_client import chat_completion
from agent_companion.core.schemas import AgentEvent, EventType, VoiceLine
from agent_companion.core.voice import safe_voice_line


class ExpressionEngine:
    """Turns internal events into character-safe user-facing speech."""

    def __init__(self, workspace: Path, character: CharacterHarness) -> None:
        self.workspace = workspace
        self.character = character
        self._config = self._load_config()
        self._client: Any | None = None

    def express(self, event: AgentEvent, user_text: str = "", session: dict[str, Any] | None = None) -> AgentEvent:
        if event.type == EventType.USER_MESSAGE:
            return event
        fallback = event.voice_line
        # The real state decides the expression; the model only writes words and
        # may vary the tone inside what that state permits.
        inputs = expression_state_from_event(event.agent_state, session)
        if not inputs["public_phase"]:
            # Expression runs before the bus stamps the phase, so derive it.
            inputs["public_phase"] = derive_public_phase(event, inputs["session_state"])
        intent = resolve_expression(**inputs)
        payload = self._llm_expression(event, user_text)
        if payload is None:
            voice_line = safe_voice_line(fallback.text, emotion=intent.clamp(fallback.emotion), sprite=fallback.sprite)
            return replace(
                event,
                voice_line=voice_line,
                agent_state=_with_expression_sync(event.agent_state, voice_line, intent),
            )

        voice_text = str(payload.get("voice_text") or fallback.text).strip()
        emotion = intent.clamp(payload.get("emotion") or fallback.emotion)
        sprite = str(payload.get("sprite") or fallback.sprite or "1")
        voice_line = safe_voice_line(voice_text, emotion=emotion, sprite=sprite)
        return replace(event, voice_line=voice_line, agent_state=_with_expression_sync(event.agent_state, voice_line, intent))

    def reload(self, character: CharacterHarness | None = None) -> None:
        if character is not None:
            self.character = character
        self._config = self._load_config()
        self._client = None

    def _llm_expression(self, event: AgentEvent, user_text: str) -> dict[str, Any] | None:
        if os.environ.get("AGENT_COMPANION_DISABLE_LLM") == "1":
            return None
        config = self._config
        if event.agent_state.get("tool") == "companion.chat":
            return None
        if config is None or config.llm.use_mock or not (config.llm.is_expression_configured or config.llm.is_configured):
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
            outcome = chat_completion(
                config.llm,
                "voice_style",
                temperature=min(max(config.llm.temperature, 0.2), 0.9),
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"你是{character_name}的表达层，只负责把 Agent 内部事件改写成角色自然短句。\n"
                            f"角色设定：{persona[:1600]}\n"
                            "规则：只输出 JSON；格式为 {\"voice_text\":\"<emo: happy>...\",\"emotion\":\"neutral|happy|thinking|alert|worried|serious\",\"sprite\":\"1\"}。"
                            "voice_text 可以在开头带一个 <emo: happy|thinking|alert|worried|serious|neutral> token；Core 会剥离 token 并同步给 TTS 与前端。"
                            "voice_text 必须短，适合朗读。禁止包含 JSON、task id、命令、路径、密钥 token、日志、退出码。"
                            "不要夸大工具结果：如果卡片只说已启动或已接收任务，不要说已经通关、完成日常或修好了。"
                            "如果 voice_lang 不是中文，voice_text 用该语言自然表达；不要解释。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
            )
            if not outcome.ok:
                # Expression degrades to the deterministic voice line; the
                # attempt is already recorded in the ledger.
                return None
            parsed = json.loads(str(outcome.value or ""))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def _load_config(self) -> Any | None:
        return load_workspace_config(self.workspace)


def _with_expression_sync(state: dict[str, Any], voice_line: VoiceLine, intent: ExpressionIntent | None = None) -> dict[str, Any]:
    next_state = dict(state or {})
    next_state["expression_sync"] = {
        "emotion": voice_line.emotion or "neutral",
        "sprite": voice_line.sprite or "1",
        "voice_style": voice_line.emotion or "neutral",
    }
    if intent is not None:
        # Carried so the shell can honour the same precedence when animating,
        # instead of inferring "busy" from whichever event arrived last.
        next_state["expression_intent"] = intent.payload()
    return next_state
