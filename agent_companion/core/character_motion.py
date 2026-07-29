"""Deterministic, local character-motion vocabulary.

The model or rule planner may choose a semantic motion, but it never controls
individual bones.  The Shell owns rendering and maps these stable names onto
VRM, Live2D, or static-character presentation.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class CharacterMotionSpec:
    name: str
    label: str
    duration_ms: int
    loop: bool
    emotion: str
    voice: str


MOTION_SPECS: dict[str, CharacterMotionSpec] = {
    "idle": CharacterMotionSpec("idle", "待机", 0, True, "neutral", "好，我安静待着。"),
    "greet": CharacterMotionSpec("greet", "挥手问候", 2200, False, "happy", "你好呀。"),
    "talk": CharacterMotionSpec("talk", "说话动作", 1800, False, "neutral", "我在。"),
    "happy": CharacterMotionSpec("happy", "开心庆祝", 2200, False, "happy", "好耶。"),
    "finger_gun": CharacterMotionSpec("finger_gun", "手指枪", 1900, False, "happy", "接住这个帅气动作。"),
    "dance": CharacterMotionSpec("dance", "跳舞", 6000, False, "happy", "来啦。"),
}

_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("finger_gun", ("比个手枪", "比手枪", "手指枪", "手枪动作", "finger gun", "finger-gun", "finger_gun")),
    ("dance", ("跳个舞", "跳舞", "舞一段", "来段舞", "dance for me", "dance")),
    ("greet", ("打个招呼", "挥挥手", "挥手", "问个好", "wave hello", "wave", "greet")),
    ("happy", ("做个开心动作", "开心动作", "庆祝一下", "庆祝动作", "celebrate", "happy pose")),
    ("talk", ("做个说话动作", "说话动作", "talk motion", "talk pose")),
    ("idle", ("回到待机", "待机动作", "安静站着", "站好", "idle")),
)


def normalize_character_motion(value: Any) -> str:
    text = re.sub(r"[\s-]+", "_", str(value or "").strip().casefold())
    return text if text in MOTION_SPECS else ""


def character_motion_from_text(value: str) -> str:
    text = " ".join(str(value or "").strip().casefold().split())
    if not text:
        return ""
    for name, aliases in _ALIASES:
        if any(alias in text for alias in aliases):
            return name
    return ""


def character_motion_payload(
    motion: Any,
    *,
    duration_ms: Any = None,
    loop: Any = None,
    intensity: Any = None,
) -> dict[str, Any] | None:
    name = normalize_character_motion(motion)
    if not name:
        return None
    spec = MOTION_SPECS[name]
    safe_duration = _safe_int(duration_ms, spec.duration_ms, 0 if name == "idle" else 400, 12_000)
    safe_loop = spec.loop if loop is None else bool(loop)
    if name != "idle" and safe_duration <= 0:
        safe_duration = spec.duration_ms
    return {
        "name": name,
        "label": spec.label,
        "duration_ms": safe_duration,
        "loop": safe_loop,
        "intensity": _safe_float(intensity, 0.8, 0.25, 1.0),
        "interruptible": True,
    }


def _safe_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _safe_float(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return round(max(minimum, min(maximum, parsed)), 2)
