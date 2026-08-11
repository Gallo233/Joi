from __future__ import annotations

import math
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

# Speech performance is richer than the six expression labels, but it remains
# bounded before it can reach a provider.  The chat model may suggest these
# values; missing, malformed or invented values collapse to the emotion's
# deterministic defaults.
DELIVERY_DEFAULTS: dict[str, dict[str, float | str]] = {
    "neutral": {"intensity": 0.34, "pace": "steady", "energy": "balanced", "pause": "natural", "emphasis": "light", "relation": "close"},
    "happy": {"intensity": 0.66, "pace": "quick", "energy": "bright", "pause": "light", "emphasis": "warm", "relation": "close"},
    "thinking": {"intensity": 0.46, "pace": "measured", "energy": "soft", "pause": "reflective", "emphasis": "keywords", "relation": "close"},
    "serious": {"intensity": 0.62, "pace": "measured", "energy": "firm", "pause": "deliberate", "emphasis": "keywords", "relation": "professional"},
    "alert": {"intensity": 0.72, "pace": "quick", "energy": "firm", "pause": "short", "emphasis": "urgent", "relation": "protective"},
    "worried": {"intensity": 0.52, "pace": "measured", "energy": "soft", "pause": "gentle", "emphasis": "caring", "relation": "supportive"},
}

DELIVERY_CHOICES: dict[str, set[str]] = {
    "pace": {"slow", "measured", "steady", "quick"},
    "energy": {"soft", "balanced", "bright", "firm"},
    "pause": {"light", "natural", "reflective", "deliberate", "short", "gentle"},
    "emphasis": {"light", "warm", "keywords", "urgent", "caring"},
    "relation": {"close", "supportive", "professional", "protective"},
}

BLOCK_PATTERNS = (
    r"\{.*\}",
    r"\b[a-zA-Z]:[\\/][^\s]+",
    r"(?<!:)\/(?:Users|home|private|tmp|var|Volumes)\/[^\s]+",
    r"\bdata/[^\s]+",
    r"\b[\w.-]+\.(png|jpg|jpeg|webp|bmp|gif|ppm|jsonl|json|log|txt|yaml|yml|py|bat|ps1|gguf|safetensors|ckpt|pth|onnx|bin)\b",
    r"\b(task|approval|selection|codex|browser|tool)[-_]?[0-9a-f]{6,}\b",
    r"\b(?:agent_cli|codex|game|browser|observe|companion|computer|mcp|files)\.[a-z0-9_.]+\b",
    r"\b\d{1,5}\s*[,，]\s*\d{1,5}\b",
    r"--[a-zA-Z0-9-]+",
    r"sk-[a-zA-Z0-9]+",
)


def safe_voice_line(
    text: str,
    fallback: str = "我整理好了，结果已经显示出来。",
    emotion: str = "neutral",
    sprite: str = "1",
    delivery: object | None = None,
) -> VoiceLine:
    cleaned = " ".join((text or "").split()).strip()
    requested_emotion = normalize_emotion(emotion)
    cleaned, token_emotion = strip_emotion_token(cleaned)
    emotion = normalize_emotion(token_emotion or requested_emotion)
    if token_emotion and emotion != requested_emotion:
        # Legacy inline tokens still win the emotion decision.  A structured
        # plan authored for the losing label must not make a worried line keep
        # happy's bright/quick performance, so rebuild it from the winner.
        delivery = None
    sprite = normalize_sprite(sprite, emotion, token_emotion=token_emotion)
    if not cleaned:
        cleaned = fallback
    for pattern in BLOCK_PATTERNS:
        if re.search(pattern, cleaned):
            cleaned = fallback
            break
    return VoiceLine(
        text=cleaned[:140],
        emotion=emotion,
        sprite=sprite,
        delivery=normalize_voice_delivery(delivery, emotion),
    )


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


def normalize_voice_delivery(value: object | None, emotion: str = "neutral") -> dict[str, float | str]:
    """Return a complete, provider-safe performance plan.

    Free-form delivery prose never crosses this boundary.  That prevents a
    model response from becoming a second prompt author or smuggling technical
    content into the voice channel.
    """

    normalized_emotion = normalize_emotion(emotion)
    result = dict(DELIVERY_DEFAULTS[normalized_emotion])
    if not isinstance(value, dict):
        return result
    try:
        intensity = float(value.get("intensity"))
    except (TypeError, ValueError):
        intensity = float(result["intensity"])
    if not math.isfinite(intensity):
        intensity = float(result["intensity"])
    result["intensity"] = round(max(0.15, min(0.9, intensity)), 2)
    for key, allowed in DELIVERY_CHOICES.items():
        candidate = str(value.get(key) or "").strip().casefold()
        if candidate in allowed:
            result[key] = candidate
    return result


def normalize_sprite(sprite: str, emotion: str, token_emotion: str = "") -> str:
    value = str(sprite or "").strip()
    if value in {"", "neutral"}:
        return EMOTION_SPRITES.get(emotion, "1")
    if value == "1" and (token_emotion or emotion != "neutral"):
        return EMOTION_SPRITES.get(emotion, "1")
    return value


def sprite_for_emotion(emotion: str, fallback: str = "1") -> str:
    return EMOTION_SPRITES.get(normalize_emotion(emotion), fallback or "1")
