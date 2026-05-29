from __future__ import annotations

import re


MEMORY_CANDIDATE_VERSION = "joi.memory_candidate.v1"

_EXPLICIT_MEMORY_RE = re.compile(r"(?:记住|记一下|记录一下|以后记得)")
_QUESTION_RE = re.compile(r"[?？]\s*$")
_UNSTABLE_RE = re.compile(r"(?:今天|现在|刚刚|这次|临时|暂时|一会儿)")
_STABLE_RE = re.compile(r"(?:以后|之后|一直|总是|默认|长期|每次)")


def chat_memory_candidate(text: str) -> dict[str, str] | None:
    value = _clean(text)
    if not value or _EXPLICIT_MEMORY_RE.search(value):
        return None
    if _QUESTION_RE.search(value) and not re.search(r"(?:叫我|称呼我|以后|之后)", value):
        return None
    candidate = _first_person_preference(value)
    if candidate is None:
        candidate = _joi_interaction_preference(value)
    if candidate is None:
        candidate = _identity_or_relationship(value)
    if candidate is None:
        return None
    kind, fact = candidate
    fact = _clean_fact(fact)
    if not fact or _looks_transient(value):
        return None
    return {
        "version": MEMORY_CANDIDATE_VERSION,
        "kind": kind,
        "fact": fact[:240],
        "text": fact[:240],
        "source": "chat",
    }


def _first_person_preference(text: str) -> tuple[str, str] | None:
    match = re.search(r"^(?:我|本人)?\s*(更喜欢|喜欢|偏好|不喜欢|讨厌|倾向于|习惯)\s*(.+)$", text, re.IGNORECASE)
    if not match:
        return None
    verb = match.group(1)
    content = _strip_subject(match.group(2))
    if not _valid_content(content):
        return None
    kind = "habit" if verb == "习惯" else "preference"
    if verb in {"不喜欢", "讨厌"}:
        fact = f"用户不喜欢{content}"
    elif verb == "习惯":
        fact = f"用户习惯{content}"
    elif verb == "倾向于":
        fact = f"用户倾向于{content}"
    else:
        fact = f"用户{verb}{content}"
    return kind, fact


def _joi_interaction_preference(text: str) -> tuple[str, str] | None:
    patterns = (
        r"^(?:以后|之后)?\s*(?:你|Joi|joi)\s*(?:回答|回复|说话)\s*(?:要|尽量|可以|请)?\s*(.+)$",
        r"^(?:我)?(?:希望|想让|想要)(?:你|Joi|joi)\s*(?:回答|回复|说话|交流|对话)?\s*(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        content = _strip_subject(match.group(1))
        if _valid_content(content):
            return "preference", f"用户希望 Joi {content}"
    return None


def _identity_or_relationship(text: str) -> tuple[str, str] | None:
    patterns = (
        r"^(?:以后|之后)?\s*(?:你|Joi|joi)\s*(?:叫我|称呼我)\s*(.+)$",
        r"^我的(?:名字|称呼|昵称)\s*(?:是|叫)\s*(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        content = _strip_subject(match.group(1))
        if _valid_content(content, min_len=1, max_len=40):
            return "relationship", f"用户希望 Joi 称呼自己为{content}"
    return None


def _looks_transient(text: str) -> bool:
    return bool(_UNSTABLE_RE.search(text) and not _STABLE_RE.search(text))


def _strip_subject(text: str) -> str:
    return _clean(text).strip(" ：:，,。.!！")


def _clean_fact(text: str) -> str:
    return _clean(text).strip(" 。.!！")


def _valid_content(text: str, *, min_len: int = 2, max_len: int = 120) -> bool:
    value = _clean(text)
    if len(value) < min_len or len(value) > max_len:
        return False
    if re.search(r"(?:密码|密钥|token|secret|api[_-]?key|日志|截图|路径|报错堆栈)", value, re.IGNORECASE):
        return False
    if re.search(r"https?://|[A-Za-z]:\\|\\\\|/(?:Users|home|tmp|var)/", value, re.IGNORECASE):
        return False
    return True


def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip()
