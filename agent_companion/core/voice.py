from __future__ import annotations

import re

from agent_companion.core.schemas import VoiceLine


EMOTION_TOKEN_PATTERN = re.compile(r"<\s*emo\s*:\s*([a-zA-Z_-]{1,32})\s*>", re.IGNORECASE)

EMOTION_ALIASES = {
    "neutral": "neutral",
    "default": "neutral",
    "happy": "happy",
    "joy": "happy",
    "success": "happy",
    "done": "happy",
    "thinking": "thinking",
    "think": "thinking",
    "working": "thinking",
    "serious": "serious",
    "focus": "serious",
    "focused": "serious",
    "alert": "alert",
    "warning": "alert",
    "warn": "alert",
    "error": "alert",
    "worried": "worried",
    "concerned": "worried",
    "failed": "worried",
}

EMOTION_SPRITES = {
    "neutral": "1",
    "thinking": "3",
    "serious": "3",
    "alert": "4",
    "worried": "4",
    "happy": "5",
}

BLOCK_PATTERNS = (
    r"\{.*\}",
    r"\b[a-zA-Z]:[\\/][^\s]+",
    r"(?<!:)\/(?:Users|home|private|tmp|var|Volumes)\/[^\s]+",
    r"\bdata/[^\s]+",
    r"\b[\w.-]+\.(png|jpg|jpeg|webp|bmp|gif|ppm|jsonl|json|log|txt|yaml|yml|py|bat|ps1|gguf|safetensors|ckpt|pth|onnx|bin)\b",
    r"\b(task|approval|selection|codex|browser|tool)[-_]?[0-9a-f]{6,}\b",
    r"\b(?:codex|game|browser|observe|companion|computer|mcp|files)\.[a-z0-9_.]+\b",
    r"\b\d{1,5}\s*[,，]\s*\d{1,5}\b",
    r"--[a-zA-Z0-9-]+",
    r"sk-[a-zA-Z0-9]+",
)


def safe_voice_line(text: str, fallback: str = "我整理好了，结果在卡片里。", emotion: str = "neutral", sprite: str = "1") -> VoiceLine:
    cleaned = " ".join((text or "").split()).strip()
    cleaned, token_emotion = strip_emotion_token(cleaned)
    emotion = normalize_emotion(token_emotion or emotion)
    sprite = normalize_sprite(sprite, emotion, token_emotion=token_emotion)
    if not cleaned:
        cleaned = fallback
    for pattern in BLOCK_PATTERNS:
        if re.search(pattern, cleaned):
            cleaned = fallback
            break
    return VoiceLine(text=cleaned[:140], emotion=emotion, sprite=sprite)


def strip_emotion_token(text: str) -> tuple[str, str]:
    found = ""

    def replace(match: re.Match[str]) -> str:
        nonlocal found
        if not found:
            found = normalize_emotion(match.group(1))
        return ""

    cleaned = EMOTION_TOKEN_PATTERN.sub(replace, text or "")
    return " ".join(cleaned.split()).strip(), found


def normalize_emotion(value: str) -> str:
    key = (value or "").strip().casefold().replace(" ", "_")
    return EMOTION_ALIASES.get(key, "neutral")


def normalize_sprite(sprite: str, emotion: str, token_emotion: str = "") -> str:
    value = str(sprite or "").strip()
    if value in {"", "neutral"}:
        return EMOTION_SPRITES.get(emotion, "1")
    if value == "1" and (token_emotion or emotion != "neutral"):
        return EMOTION_SPRITES.get(emotion, "1")
    return value


def sprite_for_emotion(emotion: str, fallback: str = "1") -> str:
    return EMOTION_SPRITES.get(normalize_emotion(emotion), fallback or "1")
