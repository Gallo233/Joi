from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from agent_companion.core.character import CharacterHarness
from agent_companion.core.voice import normalize_emotion, safe_voice_line, sprite_for_emotion


@dataclass(frozen=True)
class WatchComment:
    reply: str
    voice_text: str
    emotion: str = "neutral"
    sprite: str = "1"
    reason: str = ""


class WatchCommentaryPlanner:
    def __init__(self, workspace: Path, character: CharacterHarness, *, min_interval_seconds: float = 30.0) -> None:
        self.workspace = workspace.resolve()
        self.character = character
        self.min_interval_seconds = max(5.0, float(min_interval_seconds or 30.0))
        self._last_comment_at = time.time()
        self._last_transcript_key = ""
        self._config: Any | None = self._load_config()
        self._client: Any | None = None

    def reset(self, now: float | None = None) -> None:
        self._last_comment_at = now if now is not None else time.time()
        self._last_transcript_key = ""

    def reload(self) -> None:
        self._config = self._load_config()
        self._client = None

    def maybe_comment(self, transcript_state: dict[str, Any], *, now: float | None = None, min_interval_seconds: float | None = None) -> WatchComment | None:
        now = now if now is not None else time.time()
        interval = max(5.0, float(min_interval_seconds or self.min_interval_seconds))
        if now - self._last_comment_at < interval:
            return None
        rows = _recent_rows(transcript_state)
        if not rows:
            return None
        key = _transcript_key(rows[-8:])
        if not key or key == self._last_transcript_key:
            return None
        summary = str(transcript_state.get("summary") or "")
        comment = self._llm_comment(rows, summary) or self._fallback_comment(rows, summary)
        voice_line = safe_voice_line(
            comment.voice_text or comment.reply,
            fallback="这一段我记下来了。",
            emotion=comment.emotion,
            sprite=comment.sprite,
        )
        if not voice_line.text:
            return None
        self._last_comment_at = now
        self._last_transcript_key = key
        return WatchComment(
            reply=voice_line.text,
            voice_text=voice_line.text,
            emotion=voice_line.emotion,
            sprite=voice_line.sprite,
            reason=comment.reason or "rolling_transcript",
        )

    def _fallback_comment(self, rows: list[str], summary: str) -> WatchComment:
        latest = rows[-1]
        hint = _trim_sentence(latest, 48)
        emotion = _comment_emotion(latest)
        if any(token in latest for token in ("为什么", "怎么", "吗", "？", "?")):
            reply = f"这一段像是在抛问题：{hint}"
        elif any(token in latest for token in ("喜欢", "可爱", "开心", "成功", "完成", "厉害")):
            reply = f"这段气氛挺轻快的，我先记住重点：{hint}"
        elif any(token in latest for token in ("但是", "危险", "失败", "问题", "崩", "不能")):
            reply = f"这里像是出现了转折，我记一下：{hint}"
        elif summary:
            reply = _trim_sentence(summary, 64)
        else:
            reply = f"这一段的重点我记下来了：{hint}"
        return WatchComment(reply=reply, voice_text=reply, emotion=emotion, sprite=sprite_for_emotion(emotion), reason="fallback")

    def _llm_comment(self, rows: list[str], summary: str) -> WatchComment | None:
        if os.environ.get("AGENT_COMPANION_DISABLE_LLM") == "1":
            return None
        config = self._config
        if config is None or config.llm.use_mock or not (config.llm.is_expression_configured or config.llm.is_configured):
            return None
        try:
            from openai import OpenAI

            from agent_companion.core.config import ModelRouter

            router = ModelRouter(config.llm)
            endpoint = router.resolve("voice_style")
            if self._client is None or self._client.base_url != endpoint.base_url:
                self._client = OpenAI(api_key=endpoint.api_key, base_url=endpoint.base_url)
            character = config.primary_character if config.characters else None
            character_name = character.name if character else self.character.name
            persona = character.setting if character else self.character.persona
            voice_lang = character.voice_text_lang(config.tts.text_lang) if character else self.character.voice.get("default_lang", "zh")
            payload = {
                "rolling_summary": summary[:500],
                "new_transcript": rows[-8:],
                "voice_lang": voice_lang,
            }
            response = self._client.chat.completions.create(
                model=endpoint.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"你是{character_name}，正在陪用户看视频。\n"
                            f"角色设定：{persona[:1600]}\n"
                            "根据最近新增转写，生成一句自然、短、不过度打扰的陪看评论。"
                            "只输出 JSON：{\"reply\":\"屏幕气泡文字\",\"voice_text\":\"<emo: happy>适合朗读的一句话\",\"emotion\":\"neutral|happy|thinking|alert|worried|serious\",\"sprite\":\"1\"}。"
                            "不要复述太长，不要说自己读取了工具/转写/OCR，不要包含路径、命令、日志、token、JSON 外文本。"
                            "如果内容不足以评论，输出空 reply。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=min(max(config.llm.temperature, 0.2), 0.85),
                response_format={"type": "json_object"},
            )
            parsed = json.loads(response.choices[0].message.content or "{}")
            if not isinstance(parsed, dict):
                return None
            reply = str(parsed.get("reply") or "").strip()
            voice_text = str(parsed.get("voice_text") or reply).strip()
            if not reply and not voice_text:
                return None
            emotion = normalize_emotion(str(parsed.get("emotion") or _comment_emotion(f"{reply} {voice_text}")))
            sprite = str(parsed.get("sprite") or sprite_for_emotion(emotion))
            return WatchComment(reply=reply[:160], voice_text=voice_text[:160], emotion=emotion, sprite=sprite, reason="llm")
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


def _recent_rows(transcript_state: dict[str, Any]) -> list[str]:
    raw = transcript_state.get("recent_text")
    if not isinstance(raw, list):
        return []
    rows: list[str] = []
    for item in raw:
        text = " ".join(str(item or "").split()).strip()
        if text:
            rows.append(text[:180])
    return rows


def _transcript_key(rows: list[str]) -> str:
    normalized = "\n".join("".join(row.casefold().split()) for row in rows if row.strip())
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _trim_sentence(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split()).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(1, limit - 1)] + "…"


def _comment_emotion(text: str) -> str:
    value = text or ""
    if any(token in value for token in ("喜欢", "可爱", "开心", "成功", "完成", "厉害", "有趣")):
        return "happy"
    if any(token in value for token in ("为什么", "怎么", "吗", "？", "?", "好像", "可能")):
        return "thinking"
    if any(token in value for token in ("危险", "失败", "问题", "崩", "不能", "注意")):
        return "alert"
    return "neutral"
