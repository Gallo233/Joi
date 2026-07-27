"""Typed classification of what an action will actually do.

Sensitivity is decided by the tool being called, not by reading the words
around it. Free text -- a planner's reason, a resolved button label -- can only
raise the classification, never lower it: a tool typed as `deletion` stays a
red line even if the surrounding text looks innocuous, while a coordinate click
onto a control labelled "立即支付" is escalated to `payment`.

See docs/JOI_TDD.md §9 and §12.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Iterable

from agent_companion.core.schemas import ToolRequest


class EffectKind(str, Enum):
    """What kind of change an action makes outside Joi."""

    NONE = "none"
    DESKTOP_INPUT = "desktop_input"
    FILE_WRITE = "file_write"
    CONFIG_CHANGE = "config_change"
    PROCESS_LAUNCH = "process_launch"
    PAYMENT = "payment"
    AUTHENTICATION = "authentication"
    EXTERNAL_SEND = "external_send"
    DELETION = "deletion"
    INSTALLATION = "installation"
    PERMISSION_EXPANSION = "permission_expansion"


# The six effects that always cost the user something they cannot take back.
# No permission profile exempts them -- see PRD-ACT-003.
RED_LINE_EFFECTS: frozenset[EffectKind] = frozenset(
    {
        EffectKind.PAYMENT,
        EffectKind.AUTHENTICATION,
        EffectKind.EXTERNAL_SEND,
        EffectKind.DELETION,
        EffectKind.INSTALLATION,
        EffectKind.PERMISSION_EXPANSION,
    }
)

SENSITIVITY_ORDER = ("none", "low", "medium", "high", "red_line")

# Tool name -> the effect that tool is defined to produce. This is the primary
# classifier; anything absent is treated as UNKNOWN_EFFECT rather than safe.
TOOL_EFFECTS: dict[str, EffectKind] = {
    # Read-only observation and retrieval.
    "companion.chat": EffectKind.NONE,
    "observe.screen": EffectKind.NONE,
    "watch.recall": EffectKind.NONE,
    "browser.search": EffectKind.NONE,
    "browser.observe": EffectKind.NONE,
    "files.read": EffectKind.NONE,
    "mcp.list_tools": EffectKind.NONE,
    "vision.resolve_target": EffectKind.NONE,
    "vision.select_target": EffectKind.NONE,
    # Desktop and browser input.
    "computer.click": EffectKind.DESKTOP_INPUT,
    "computer.double_click": EffectKind.DESKTOP_INPUT,
    "computer.drag": EffectKind.DESKTOP_INPUT,
    "computer.type_text": EffectKind.DESKTOP_INPUT,
    "computer.scroll": EffectKind.DESKTOP_INPUT,
    "computer.hotkey": EffectKind.DESKTOP_INPUT,
    "computer.open_app": EffectKind.PROCESS_LAUNCH,
    "computer.workflow": EffectKind.DESKTOP_INPUT,
    "browser.click": EffectKind.DESKTOP_INPUT,
    "browser.type": EffectKind.DESKTOP_INPUT,
    # Local writes and configuration.
    "files.write_workspace": EffectKind.FILE_WRITE,
    "runtime.update_config": EffectKind.CONFIG_CHANGE,
    # Sub-process execution.
    "shell.run": EffectKind.PROCESS_LAUNCH,
    "codex.run": EffectKind.PROCESS_LAUNCH,
    "agent_cli.run": EffectKind.PROCESS_LAUNCH,
    "skill.run": EffectKind.PROCESS_LAUNCH,
    "game.ok_ww.run": EffectKind.PROCESS_LAUNCH,
    # Red lines.
    "files.delete": EffectKind.DELETION,
    "package.install": EffectKind.INSTALLATION,
    "git.push": EffectKind.EXTERNAL_SEND,
    "external.launch_admin": EffectKind.PERMISSION_EXPANSION,
}

# An unregistered tool is not assumed harmless; it lands here.
UNKNOWN_EFFECT = EffectKind.DESKTOP_INPUT

_EFFECT_SENSITIVITY: dict[EffectKind, str] = {
    EffectKind.NONE: "none",
    EffectKind.DESKTOP_INPUT: "medium",
    EffectKind.FILE_WRITE: "medium",
    EffectKind.CONFIG_CHANGE: "high",
    EffectKind.PROCESS_LAUNCH: "high",
}

_REQUIRED_CAPABILITIES: dict[EffectKind, tuple[str, ...]] = {
    EffectKind.DESKTOP_INPUT: ("accessibility", "screen_recording"),
    EffectKind.PROCESS_LAUNCH: ("apple_events",),
}

# Free-text markers that can raise a classification. Coordinate clicks carry no
# verb of their own -- `computer.click` only ever sees x/y -- so the resolved
# target label the planner puts in ToolRequest.reason is often the only place a
# payment or login control is named. Matching is deliberately generous: a
# needless confirmation costs one click, a missed one spends real money.
EFFECT_MARKERS: dict[EffectKind, tuple[str, ...]] = {
    EffectKind.PAYMENT: (
        "payment", "pay now", "pay ", "checkout", "check out", "place order", "buy now",
        "billing", "credit card", "debit card", "subscribe", "purchase",
        "付款", "支付", "结账", "结算", "下单", "购买", "充值", "银行卡", "信用卡", "确认支付", "立即购买", "续费",
    ),
    EffectKind.AUTHENTICATION: (
        "login", "log in", "sign in", "sign-in", "signin", "password", "passcode",
        "authorize", "authorise", "oauth", "two-factor", "2fa", "verification code", "credential",
        "登录", "登陆", "授权", "密码", "验证码", "身份验证", "扫码登录",
    ),
    EffectKind.EXTERNAL_SEND: (
        "send_message", "send message", "send email", "send mail", "reply all",
        "publish", "submit post", "tweet", "share to",
        "发消息", "发送消息", "发邮件", "发送邮件", "回复全部", "发布", "转发", "群发",
    ),
    EffectKind.DELETION: (
        "delete", "permanently remove", "empty trash", "erase", "wipe",
        "删除", "永久删除", "清空", "抹掉", "移除",
    ),
    EffectKind.INSTALLATION: (
        "install", "uninstall", "安装", "卸载", "装载",
    ),
    EffectKind.PERMISSION_EXPANSION: (
        "expand scope", "expand_scope", "grant access", "allow always", "always allow", "full access",
        "扩权", "扩大范围", "始终允许", "永久允许", "完全访问",
    ),
}

# Argument keys that can name what an action actually touches.
_SIGNAL_KEYS = ("action", "intent", "goal", "text", "url", "query", "target", "label", "app_name", "workflow")

# Excluded from the digest because they vary between otherwise identical
# actions, which would break both dedup and replay detection.
_VOLATILE_ARG_KEYS = frozenset({"memory_context"})


def marker_effect(*signals: Any) -> EffectKind | None:
    """Return the red-line effect any signal names, or None."""
    haystack = " ".join(str(value or "").casefold() for value in signals if str(value or "").strip())
    if not haystack:
        return None
    for effect in sorted(EFFECT_MARKERS, key=lambda item: item.value):
        if any(marker in haystack for marker in EFFECT_MARKERS[effect]):
            return effect
    return None


def request_signals(request: ToolRequest) -> list[str]:
    """Collect every free-text field that could name a sensitive action."""
    signals = [request.reason]
    for key in _SIGNAL_KEYS:
        value = request.arguments.get(key)
        if isinstance(value, (str, int, float)):
            signals.append(str(value)[:400])
    return [signal for signal in signals if signal]


@dataclass(frozen=True)
class ActionIntent:
    """The normalized description policy decides on, never the raw request."""

    tool: str
    effect_kind: EffectKind
    typed_effect: EffectKind
    sensitivity: str
    escalated_by: str
    normalized_args_digest: str
    idempotency_key: str
    required_capabilities: tuple[str, ...]

    @property
    def is_red_line(self) -> bool:
        return self.effect_kind in RED_LINE_EFFECTS

    def payload(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "effect_kind": self.effect_kind.value,
            "typed_effect": self.typed_effect.value,
            "sensitivity": self.sensitivity,
            "escalated_by": self.escalated_by,
            "normalized_args_digest": self.normalized_args_digest,
            "idempotency_key": self.idempotency_key,
            "required_capabilities": list(self.required_capabilities),
        }

    @classmethod
    def from_request(cls, request: ToolRequest, extra_signals: Iterable[Any] = ()) -> "ActionIntent":
        tool = str(request.name or "").strip()
        typed = TOOL_EFFECTS.get(tool, UNKNOWN_EFFECT)
        effect, escalated_by = typed, ""
        if typed not in RED_LINE_EFFECTS:
            # Text can only raise. A tool already typed as a red line keeps its
            # own classification so a bland label cannot talk it down.
            raised = marker_effect(tool, *request_signals(request), *extra_signals)
            if raised is not None:
                effect, escalated_by = raised, "signal_marker"
        digest = _arguments_digest(request.arguments)
        return cls(
            tool=tool,
            effect_kind=effect,
            typed_effect=typed,
            sensitivity="red_line" if effect in RED_LINE_EFFECTS else _EFFECT_SENSITIVITY.get(effect, "medium"),
            escalated_by=escalated_by,
            normalized_args_digest=digest,
            idempotency_key=hashlib.sha256(f"{tool}\0{digest}".encode("utf-8")).hexdigest(),
            required_capabilities=_REQUIRED_CAPABILITIES.get(effect, ()),
        )


def _arguments_digest(arguments: dict[str, Any]) -> str:
    canonical = {key: value for key, value in sorted((arguments or {}).items()) if key not in _VOLATILE_ARG_KEYS}
    try:
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        encoded = repr(canonical)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
