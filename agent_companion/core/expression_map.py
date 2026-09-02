"""What the character is allowed to look like, given what is actually happening.

The character is the interface users read state from. If it smiles while a
session is paused waiting for them, or looks calm while a permission is
missing, the interface is lying — and users act on that. So expression is
derived from the run's real state, not suggested by the model.

The model still writes the words. It may also suggest a tone, but only within
the set the real state permits: it can pick between neutral shades of "busy",
and it can never turn a risk state into a cheerful one. A late TOOL_COMPLETED
arriving after a session paused must not repaint the character happy
(TDD §8.2, PRD §13.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from agent_companion.core.skill_manifest import skill_id_for_tool


# The only expressions the runtime emits, matching expression.py's vocabulary.
EMOTIONS = ("neutral", "happy", "thinking", "alert", "worried", "serious")

# Highest-priority condition first. A state further up this list cannot be
# overwritten by one below it, whatever order the events happen to arrive in.
OVERRIDE_PRIORITY = (
    "approval",
    "takeover",
    "permission_missing",
    "failed",
    "acting",
    "thinking",
    "received",
    "idle",
)

# Conditions where a reassuring face would misrepresent the situation.
_RISK_CONDITIONS = frozenset({"approval", "takeover", "permission_missing", "failed"})

_CONDITION_EMOTION = {
    "approval": "serious",
    "takeover": "serious",
    "permission_missing": "worried",
    "failed": "worried",
    "acting": "alert",
    "thinking": "thinking",
    "received": "neutral",
    "idle": "neutral",
}

# Phases where speaking is either pointless or actively unwanted.
_SILENT_CONDITIONS = frozenset({"idle"})

# Talking has no external effect to misreport, so a reply may carry any tone
# the character knows how to wear.
CONVERSATION_EMOTIONS: tuple[str, ...] = ("neutral", "happy", "thinking", "alert", "worried", "serious")


@dataclass(frozen=True)
class ExpressionIntent:
    """The expression the runtime commits to, and the room left for the model."""

    emotion: str
    condition: str
    may_speak: bool
    allowed_emotions: tuple[str, ...]
    locked: bool
    reason: str

    def clamp(self, suggested: Any) -> str:
        """Accept the model's tone only if the real state permits it."""
        candidate = str(suggested or "").strip().casefold()
        if self.locked or candidate not in self.allowed_emotions:
            return self.emotion
        return candidate

    def payload(self) -> dict[str, Any]:
        return {
            "emotion": self.emotion,
            "condition": self.condition,
            "may_speak": self.may_speak,
            "allowed_emotions": list(self.allowed_emotions),
            "locked": self.locked,
            "reason": self.reason,
        }


def resolve_condition(
    public_phase: str,
    *,
    session_state: str = "",
    risk: str = "",
    permission_missing: bool = False,
    taken_over: bool = False,
    verified: bool = True,
) -> str:
    """Name the highest-priority thing currently true."""
    phase = str(public_phase or "idle").strip().casefold()
    state = str(session_state or "").strip().casefold()

    if state == "waiting_approval" or phase == "waiting":
        return "approval"
    if taken_over:
        return "takeover"
    if permission_missing:
        return "permission_missing"
    if phase == "failed":
        return "failed"
    if state == "paused":
        # A paused session is waiting on a person, so it reads as approval
        # rather than as whatever the last tool event happened to say.
        return "approval"
    if phase == "done":
        # Success that was never verified is not success worth celebrating.
        return "done" if verified else "failed"
    if phase == "acting":
        return "failed" if str(risk or "").casefold() == "high" and not verified else "acting"
    if phase in {"thinking", "understanding"}:
        return "thinking"
    if phase == "received":
        return "received"
    return "idle"


def resolve_expression(
    public_phase: str,
    *,
    session_state: str = "",
    risk: str = "",
    permission_missing: bool = False,
    taken_over: bool = False,
    verified: bool = True,
    conversational: bool = False,
) -> ExpressionIntent:
    """Map real state onto an expression, and say how much the model may vary it."""
    condition = resolve_condition(
        public_phase,
        session_state=session_state,
        risk=risk,
        permission_missing=permission_missing,
        taken_over=taken_over,
        verified=verified,
    )
    if condition == "done" and conversational:
        # A reply is not a task result. The reason a finished task may only
        # look happy or neutral is that the character must not celebrate work
        # it cannot show it did -- but a conversation makes no such claim, so
        # there is nothing to guard and the tone should follow what was said.
        # Answering "that sounds hard" with a beaming smile is the bug.
        #
        # The risk conditions above still win: this only relaxes success.
        return ExpressionIntent(
            emotion="neutral",
            condition="conversation",
            may_speak=True,
            allowed_emotions=CONVERSATION_EMOTIONS,
            locked=False,
            reason="conversation_turn",
        )
    if condition == "done":
        return ExpressionIntent(
            emotion="happy",
            condition="done",
            may_speak=True,
            allowed_emotions=("happy", "neutral"),
            locked=False,
            reason="verified_success",
        )
    emotion = _CONDITION_EMOTION.get(condition, "neutral")
    locked = condition in _RISK_CONDITIONS
    if locked:
        # No tone shopping while the user is being asked for something or
        # something went wrong.
        allowed: tuple[str, ...] = (emotion,)
    elif condition == "acting":
        allowed = ("alert", "thinking")
    elif condition == "thinking":
        allowed = ("thinking", "neutral")
    else:
        allowed = ("neutral", "thinking")
    return ExpressionIntent(
        emotion=emotion,
        condition=condition,
        may_speak=condition not in _SILENT_CONDITIONS,
        allowed_emotions=allowed,
        locked=locked,
        reason=f"{condition}_state",
    )


def outranks(current: str, incoming: str) -> bool:
    """True when `current` must not be replaced by `incoming`.

    Events arrive out of order; this is what stops a late success event from
    overwriting the paused state the user is actually looking at.
    """
    order = {name: index for index, name in enumerate(OVERRIDE_PRIORITY)}
    # "done" is not an override condition; it ranks below everything that needs
    # the user, so treat it as the weakest.
    current_rank = order.get(current, len(OVERRIDE_PRIORITY))
    incoming_rank = order.get(incoming, len(OVERRIDE_PRIORITY))
    return current_rank < incoming_rank


def expression_state_from_event(state: dict[str, Any] | None, session: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pull the inputs the mapping needs out of an event's agent_state."""
    state = state if isinstance(state, dict) else {}
    session = session if isinstance(session, dict) else {}
    verification = state.get("post_action_verification") if isinstance(state.get("post_action_verification"), dict) else {}
    return {
        "public_phase": str(state.get("public_phase") or ""),
        "session_state": str(session.get("state") or ""),
        "risk": str(state.get("risk") or ""),
        "permission_missing": bool(state.get("permission_required") or state.get("permission_missing")),
        "taken_over": str(session.get("pause_reason") or "") == "taken_over",
        "verified": _is_verified(state, verification),
        # Which tool produced the event decides whether this was a turn of
        # conversation or a piece of work, and the registration contract
        # already records that -- no second table of tool names here.
        "conversational": skill_id_for_tool(str(state.get("tool") or "")) == "joi.companion.chat",
    }


def _is_verified(state: dict[str, Any], verification: dict[str, Any]) -> bool:
    if verification:
        return str(verification.get("status") or "") not in {"likely_noop", "unverified", "failed"}
    computer = state.get("computer_use") if isinstance(state.get("computer_use"), dict) else {}
    if computer:
        # A desktop action with no after-observation has not demonstrated
        # anything, so it does not earn a success expression.
        return bool(computer.get("observation") or computer.get("after_artifact"))
    return True


def allowed_emotion_names(intents: Iterable[ExpressionIntent]) -> set[str]:
    names: set[str] = set()
    for intent in intents:
        names.update(intent.allowed_emotions)
    return names
