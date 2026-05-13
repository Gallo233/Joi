from __future__ import annotations

import re

from agent_companion.core.schemas import VoiceLine


BLOCK_PATTERNS = (
    r"\{.*\}",
    r"\b[a-zA-Z]:[\\/][^\s]+",
    r"\bdata/[^\s]+",
    r"\b[\w.-]+\.(jsonl|log|txt|yaml|yml|py|bat|ps1)\b",
    r"\b(task|codex|browser|tool)[-_]?[0-9a-f]{6,}\b",
    r"--[a-zA-Z0-9-]+",
    r"sk-[a-zA-Z0-9]+",
)


def safe_voice_line(text: str, fallback: str = "我整理好了，结果在卡片里。", emotion: str = "neutral", sprite: str = "1") -> VoiceLine:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        cleaned = fallback
    for pattern in BLOCK_PATTERNS:
        if re.search(pattern, cleaned):
            cleaned = fallback
            break
    return VoiceLine(text=cleaned[:140], emotion=emotion, sprite=sprite)

