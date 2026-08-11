"""Keep the language of displayed text separate from the language of speech.

A character can have a Japanese voice and still answer a Chinese message on
screen in Chinese.  The model sees the original message and makes the semantic
language decision; this module adds a deterministic script hint and catches
obvious cross-script violations without pretending that a local heuristic can
distinguish every language written with the Latin alphabet.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DisplayLanguagePolicy:
    code: str
    label: str
    script: str

    @property
    def prompt_instruction(self) -> str:
        return (
            "reply 是屏幕文字，必须使用用户本轮输入所使用的同一种自然语言"
            f"（本轮提示：{self.label}）。不要因为角色设定、角色姓名或配音语言而翻译 reply；"
            "用户刻意保留的专有名词可以维持原文。"
        )


_SCRIPT_LABELS = {
    "zh": ("中文", "han"),
    "ja": ("日本語", "kana"),
    "ko": ("한국어", "hangul"),
    "en": ("English", "latin"),
    "latn": ("与用户相同的拉丁字母语言；从措辞判断具体语言，不要默认成英语", "latin"),
    "cyrl": ("与用户相同的西里尔字母语言", "cyrillic"),
    "arab": ("与用户相同的阿拉伯字母语言", "arabic"),
    "deva": ("与用户相同的天城文字语言", "devanagari"),
    "thai": ("ภาษาไทย", "thai"),
    "hebr": ("与用户相同的希伯来字母语言", "hebrew"),
    "grek": ("与用户相同的希腊字母语言", "greek"),
    "auto": ("与用户本轮输入完全相同的自然语言", "unknown"),
}

_VOICE_LANGUAGE_LABELS = {
    "zh": "中文",
    "yue": "粤语",
    "ja": "日本語",
    "ko": "한국어",
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
    "pt": "Português",
    "ru": "Русский",
    "ar": "العربية",
}

_ENGLISH_MARKERS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "can",
        "could",
        "do",
        "hello",
        "hi",
        "how",
        "i",
        "is",
        "please",
        "the",
        "this",
        "what",
        "who",
        "why",
        "you",
        "your",
    }
)


def display_language_policy(text: str) -> DisplayLanguagePolicy:
    counts = _script_counts(text)
    if counts["kana"]:
        code = "ja"
    elif counts["hangul"]:
        code = "ko"
    elif counts["han"]:
        code = "zh"
    else:
        candidates = ("arabic", "cyrillic", "devanagari", "thai", "hebrew", "greek", "latin")
        script = max(candidates, key=lambda name: counts[name])
        if not counts[script]:
            code = "auto"
        elif script == "latin":
            words = {word.casefold() for word in _latin_words(text)}
            code = "en" if words & _ENGLISH_MARKERS else "latn"
        else:
            code = {
                "arabic": "arab",
                "cyrillic": "cyrl",
                "devanagari": "deva",
                "thai": "thai",
                "hebrew": "hebr",
                "greek": "grek",
            }[script]
    label, script = _SCRIPT_LABELS[code]
    return DisplayLanguagePolicy(code, label, script)


def reply_language_instruction(text: str) -> str:
    """A high-priority instruction for a user-visible answer channel."""

    return display_language_policy(text).prompt_instruction


def voice_language_label(locale: str) -> str:
    normalized = str(locale or "").strip().replace("_", "-").casefold()
    base = normalized.split("-")[0]
    return _VOICE_LANGUAGE_LABELS.get(base, normalized or "用户选择的配音语言")


def obvious_language_mismatch(user_text: str, reply: str) -> bool:
    """Return true only for a clear script-level contract violation.

    This is deliberately conservative.  Chinese and Japanese share Han
    characters, while Spanish and English share Latin letters; uncertain cases
    are left to the model instead of being repeatedly "corrected" into the
    wrong language.
    """

    return _obvious_policy_mismatch(display_language_policy(user_text), reply)


def obvious_voice_language_mismatch(locale: str, voice_text: str) -> bool:
    """Catch a voice line clearly written for a different selected locale."""

    normalized = str(locale or "").strip().replace("_", "-").casefold().split("-")[0]
    code = {"zh": "zh", "yue": "zh", "ja": "ja", "ko": "ko", "en": "en"}.get(normalized)
    if not code:
        return False
    label, script = _SCRIPT_LABELS[code]
    return _obvious_policy_mismatch(DisplayLanguagePolicy(code, label, script), voice_text)


def _obvious_policy_mismatch(policy: DisplayLanguagePolicy, reply: str) -> bool:
    counts = _script_counts(reply)
    if not reply.strip() or policy.script == "unknown":
        return False
    if policy.code == "zh":
        return bool(counts["kana"] or counts["hangul"] or counts["arabic"] or counts["cyrillic"])
    if policy.code == "ja":
        foreign = counts["hangul"] + counts["arabic"] + counts["cyrillic"] + counts["devanagari"]
        return bool(foreign or (not counts["kana"] and not counts["han"] and counts["latin"] >= 4))
    if policy.script == "latin":
        foreign = counts["han"] + counts["kana"] + counts["hangul"] + counts["arabic"] + counts["cyrillic"]
        return foreign >= max(2, counts["latin"] * 2)
    expected = counts[policy.script]
    foreign = max((value for name, value in counts.items() if name not in {policy.script, "latin"}), default=0)
    return expected == 0 and foreign > 0


def language_mismatch_fallback(policy: DisplayLanguagePolicy) -> str:
    """Fail in-language for the scripts Joi can name with confidence."""

    return {
        "zh": "刚才的回答语言没有正确匹配你的输入。请再说一次，我会继续用中文回答。",
        "ja": "返答の言語が入力と一致しませんでした。もう一度送ってください。日本語で続けます。",
        "ko": "답변 언어가 입력과 일치하지 않았어요. 다시 보내 주시면 한국어로 계속할게요.",
        "en": "The reply language did not match your message. Please send it again, and I’ll continue in English.",
    }.get(policy.code, "")


def _latin_words(text: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for char in text:
        if _script_of(char) == "latin":
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def _script_counts(text: str) -> dict[str, int]:
    counts = {name: 0 for name in ("han", "kana", "hangul", "latin", "cyrillic", "arabic", "devanagari", "thai", "hebrew", "greek")}
    for char in text or "":
        script = _script_of(char)
        if script:
            counts[script] += 1
    return counts


def _script_of(char: str) -> str:
    code = ord(char)
    if 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
        return "kana"
    if 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
        return "han"
    if 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F or 0xAC00 <= code <= 0xD7AF:
        return "hangul"
    if 0x0041 <= code <= 0x005A or 0x0061 <= code <= 0x007A or 0x00C0 <= code <= 0x024F:
        return "latin"
    if 0x0400 <= code <= 0x052F:
        return "cyrillic"
    if 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F or 0x08A0 <= code <= 0x08FF:
        return "arabic"
    if 0x0900 <= code <= 0x097F:
        return "devanagari"
    if 0x0E00 <= code <= 0x0E7F:
        return "thai"
    if 0x0590 <= code <= 0x05FF:
        return "hebrew"
    if 0x0370 <= code <= 0x03FF:
        return "greek"
    return ""
