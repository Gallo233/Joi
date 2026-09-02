"""What to do when the optional CUA driver fails mid-action.

`cua-driver` is an optional enhancement, not a security boundary and not a
release dependency. When one of its calls fails there is exactly one question
that matters: did the keystroke or click reach the application or not?

- If it provably never left -- the binary is missing, the daemon is not
  running, the window could not be bound, the arguments were rejected -- then
  nothing happened outside and the native driver may simply do the work.
- If we cannot tell -- a timeout, a killed process, a reply we cannot parse --
  then the action may already have landed. Retrying on the native driver would
  be a *second* click, and for a payment or a send that second click is the
  whole problem. Those cases re-observe first and hand the decision back.

So the classifier is deliberately pessimistic: anything not recognised as a
clean pre-delivery failure is treated as uncertain (TDD §9.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent_companion.core.action_intent import EffectKind


class Delivery(str, Enum):
    """Whether the action reached the application."""

    NOT_DELIVERED = "not_delivered"
    UNCERTAIN = "uncertain"
    DELIVERED = "delivered"


# Errors raised before the request could reach the daemon. Everything here has
# to be something that cannot happen once input is in flight.
_PRE_DELIVERY_MARKERS = (
    "cua_driver_missing",
    "cua_driver_unavailable",
    "no such file or directory",
    "command not found",
    "executable not found",
    "connection refused",
    "could not connect",
    "daemon not running",
    "not running",
    "requires x and y",
    "requires start and end coordinates",
    "requires text",
    "requires keys",
    "requires app name",
    "unsupported cua action",
    "unable to bind",
    "window not found",
    "no matching window",
    "process not found",
    "invalid arguments",
    "unknown tool",
)

# Markers that positively indicate the call was in flight or completed.
_IN_FLIGHT_MARKERS = (
    "timeout",
    "timed out",
    "terminated",
    "killed",
    "broken pipe",
    "connection reset",
    "eof",
)

# Effects that change nothing outside Joi can always be retried.
_REPLAY_SAFE_EFFECTS = frozenset({EffectKind.NONE})


@dataclass(frozen=True)
class FallbackPlan:
    """The decision: retry, re-observe, or stop and ask."""

    delivery: Delivery
    retry_on_native: bool
    must_reobserve: bool
    pause_reason: str
    reason: str

    def payload(self) -> dict[str, Any]:
        return {
            "delivery": self.delivery.value,
            "retry_on_native": self.retry_on_native,
            "must_reobserve": self.must_reobserve,
            "pause_reason": self.pause_reason,
            "reason": self.reason,
        }


def classify_delivery(error: str) -> Delivery:
    """Judge from the failure text whether the action ever left Joi."""
    text = str(error or "").casefold()
    if not text:
        return Delivery.UNCERTAIN
    if any(marker in text for marker in _IN_FLIGHT_MARKERS):
        return Delivery.UNCERTAIN
    if any(marker in text for marker in _PRE_DELIVERY_MARKERS):
        return Delivery.NOT_DELIVERED
    # Unrecognised failures are treated as possibly-delivered on purpose: the
    # cost of a needless re-observation is a screenshot, the cost of a wrong
    # guess is a duplicated real-world action.
    return Delivery.UNCERTAIN


def plan_fallback(error: str, effect_kind: EffectKind = EffectKind.DESKTOP_INPUT) -> FallbackPlan:
    """Decide how to recover from a failed CUA call."""
    delivery = classify_delivery(error)
    if effect_kind in _REPLAY_SAFE_EFFECTS:
        # Observation and other no-effect calls can be repeated freely; there is
        # nothing outside Joi to duplicate.
        return FallbackPlan(delivery, True, False, "", "no_external_effect")
    if delivery is Delivery.NOT_DELIVERED:
        return FallbackPlan(delivery, True, False, "", "cua_never_delivered")
    return FallbackPlan(
        delivery,
        retry_on_native=False,
        must_reobserve=True,
        pause_reason="cua_delivery_uncertain",
        reason="cua_may_have_acted",
    )


class FallbackComputerUseBackend:
    """Runs actions on CUA, falling back to native only when that is safe.

    Holds both drivers so the decision can be made per action rather than once
    per session: a driver that works for three steps and dies on the fourth is
    the case that matters.
    """

    def __init__(self, primary: Any, native_factory: Any, *, effect_kind_for: Any = None) -> None:
        self.primary = primary
        self._native_factory = native_factory
        self._native: Any = None
        self._effect_kind_for = effect_kind_for or (lambda action: EffectKind.DESKTOP_INPUT)
        self.last_plan: FallbackPlan | None = None
        self.actual_driver = "cua"

    def _native_backend(self) -> Any:
        if self._native is None:
            self._native = self._native_factory()
        return self._native

    def observe(self, target: str = "active_window", query: str = "") -> Any:
        try:
            result = self.primary.observe(target=target, query=query)
            self.actual_driver = "cua"
            return result
        except Exception as exc:
            # Observation has no external effect, so falling back is always safe.
            self.actual_driver = "native"
            self.last_plan = plan_fallback(f"{type(exc).__name__}:{exc}", EffectKind.NONE)
            return self._native_backend().observe(target=target, query=query)

    def perform(self, action: Any) -> Any:
        result = self.primary.perform(action)
        if getattr(result, "ok", False):
            self.actual_driver = "cua"
            self.last_plan = None
            return result

        plan = plan_fallback(getattr(result, "error", "") or "", self._effect_kind_for(action))
        self.last_plan = plan
        if not plan.retry_on_native:
            # Do not replay. Report the ambiguity so the caller re-observes and
            # the session pauses rather than acting a second time.
            self.actual_driver = "cua"
            return _uncertain_result(result, action, plan)
        self.actual_driver = "native"
        return self._native_backend().perform(action)

    def perform_sequence(self, actions: Any, settle_ms: int = 220) -> Any:
        result = self.primary.perform_sequence(actions, settle_ms=settle_ms)
        if getattr(result, "ok", False):
            self.actual_driver = "cua"
            self.last_plan = None
            return result
        # A partially-executed sequence is uncertain by construction: some steps
        # may have landed. Never restart the whole workflow on native.
        plan = FallbackPlan(Delivery.UNCERTAIN, False, True, "cua_delivery_uncertain", "cua_sequence_partial")
        self.last_plan = plan
        return _uncertain_result(result, getattr(result, "action", None), plan)

    def __getattr__(self, name: str) -> Any:
        # Anything not part of the fallback contract belongs to the primary.
        return getattr(self.primary, name)


def _uncertain_result(result: Any, action: Any, plan: FallbackPlan) -> Any:
    from agent_companion.core.computer_use.schemas import ComputerUseResult

    detail = getattr(result, "error", "") or "CUA call failed"
    return ComputerUseResult(
        False,
        action=action if action is not None else getattr(result, "action", None),
        error=f"{plan.pause_reason}:{detail}"[:600],
        detail="CUA 可能已经执行了这一步，先重新观察再决定，不会直接重试。",
    )
