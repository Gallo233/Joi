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

# Ordered most specific first: "比个手枪" also contains "手枪", and a bare
# "动作" phrase must not win over the motion it qualifies.
#
# These are matched as substrings of the whole message, so they cover ordinary
# phrasing without a model in the loop -- "能不能给我跳个舞", "来跳舞吧",
# "帮我挥挥手" all land through the same table. Deliberately still a table:
# a motion is something the character does with its body, and letting free
# text reach it through anything fuzzier is how a request to *talk about*
# dancing becomes a request to dance.
_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "finger_gun",
        ("比个手枪", "比手枪", "手指枪", "手枪动作", "比个枪", "finger gun", "finger-gun", "finger_gun", "指鉄砲"),
    ),
    (
        "dance",
        (
            "跳个舞", "跳支舞", "跳段舞", "跳一下舞", "跳舞", "舞一段", "来段舞", "来个舞",
            "扭一个", "蹦个迪", "dance for me", "do a dance", "dance",
            # Japanese: the app ships zh/ja/en characters, so a motion that
            # only answers Chinese phrasing is unreachable for half of them.
            "踊って", "踊る", "ダンス",
        ),
    ),
    (
        "greet",
        (
            "打个招呼", "打招呼", "挥挥手", "挥个手", "挥手", "问个好", "问好", "招呼一下",
            "say hi", "say hello", "wave hello", "wave", "greet",
            "手を振って", "挨拶して", "こんにちは",
        ),
    ),
    (
        "happy",
        (
            "做个开心动作", "开心动作", "开心一下", "庆祝一下", "庆祝动作", "庆祝", "高兴一下",
            "撒花", "celebrate", "happy pose", "cheer",
            "喜んで", "祝って",
        ),
    ),
    (
        "talk",
        ("做个说话动作", "说话动作", "talk motion", "talk pose"),
    ),
    (
        "idle",
        (
            "回到待机", "待机动作", "安静站着", "站好", "别动了", "停下动作", "休息一下",
            "stand still", "idle",
            "止まって", "待機",
        ),
    ),
)


def normalize_character_motion(value: Any) -> str:
    text = re.sub(r"[\s-]+", "_", str(value or "").strip().casefold())
    return text if text in MOTION_SPECS else ""


# Phrases that make a message *about* a motion rather than a request for one.
# Widening the aliases to catch ordinary phrasing also catches "聊聊跳舞这个
# 话题", and answering that by dancing is worse than not recognising it.
_DISCUSSION_MARKERS: tuple[str, ...] = (
    "聊聊", "聊一下", "讨论", "话题", "是什么", "什么意思", "怎么样", "为什么",
    "介绍一下", "解释", "教程", "历史", "talk about", "what is", "explain",
)


def character_motion_from_text(value: str) -> str:
    text = " ".join(str(value or "").strip().casefold().split())
    if not text:
        return ""
    if any(marker in text for marker in _DISCUSSION_MARKERS):
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
