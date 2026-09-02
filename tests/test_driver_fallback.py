"""A failed CUA call must never become a second real-world action.

The dangerous case is not a driver that is missing -- that one is obvious and
safe. It is a driver that accepted the click and then timed out, where retrying
on the native driver clicks "Pay" twice.
"""

from __future__ import annotations

import unittest

from agent_companion.core.action_intent import EffectKind
from agent_companion.core.computer_use.driver_fallback import (
    Delivery,
    FallbackComputerUseBackend,
    classify_delivery,
    plan_fallback,
)
from agent_companion.core.computer_use.schemas import ComputerAction, ComputerUseResult


class _FakeBackend:
    def __init__(self, results: list[ComputerUseResult] | None = None, observe_error: Exception | None = None) -> None:
        self.results = list(results or [])
        self.observe_error = observe_error
        self.performed: list[ComputerAction] = []
        self.observed = 0

    def perform(self, action: ComputerAction) -> ComputerUseResult:
        self.performed.append(action)
        return self.results.pop(0) if self.results else ComputerUseResult(True, action=action, summary="ok")

    def perform_sequence(self, actions, settle_ms: int = 220) -> ComputerUseResult:
        return self.results.pop(0) if self.results else ComputerUseResult(True, action=ComputerAction("workflow"))

    def observe(self, target: str = "active_window", query: str = "") -> str:
        self.observed += 1
        if self.observe_error is not None:
            raise self.observe_error
        return f"observation:{target}"


CLICK = ComputerAction("click", x=10, y=20)


class DeliveryClassificationTests(unittest.TestCase):
    def test_setup_failures_provably_never_reached_the_app(self) -> None:
        for error in (
            "cua_driver_failed:FileNotFoundError:no such file or directory",
            "connection refused",
            "cua daemon not running",
            "click requires x and y",
            "unsupported CUA action: teleport",
            "window not found for pid 42",
        ):
            with self.subTest(error=error):
                self.assertEqual(classify_delivery(error), Delivery.NOT_DELIVERED)

    def test_in_flight_failures_are_uncertain(self) -> None:
        for error in (
            "cua_driver_failed:TimeoutExpired:Command timed out after 20s",
            "process terminated",
            "broken pipe",
            "connection reset by peer",
        ):
            with self.subTest(error=error):
                self.assertEqual(classify_delivery(error), Delivery.UNCERTAIN)

    def test_an_unrecognised_failure_is_treated_as_possibly_delivered(self) -> None:
        # Pessimism is the point: a needless re-observation costs a screenshot,
        # a wrong guess costs a duplicated action.
        self.assertEqual(classify_delivery("something nobody anticipated"), Delivery.UNCERTAIN)
        self.assertEqual(classify_delivery(""), Delivery.UNCERTAIN)


class FallbackPlanTests(unittest.TestCase):
    def test_undelivered_input_may_be_retried_natively(self) -> None:
        plan = plan_fallback("connection refused", EffectKind.DESKTOP_INPUT)
        self.assertTrue(plan.retry_on_native)
        self.assertFalse(plan.must_reobserve)

    def test_uncertain_input_is_never_retried(self) -> None:
        plan = plan_fallback("timed out", EffectKind.DESKTOP_INPUT)
        self.assertFalse(plan.retry_on_native)
        self.assertTrue(plan.must_reobserve)
        self.assertEqual(plan.pause_reason, "cua_delivery_uncertain")

    def test_a_payment_click_is_not_retried_on_an_unknown_failure(self) -> None:
        plan = plan_fallback("weird backend hiccup", EffectKind.PAYMENT)
        self.assertFalse(plan.retry_on_native)
        self.assertTrue(plan.must_reobserve)

    def test_calls_with_no_external_effect_are_always_replayable(self) -> None:
        plan = plan_fallback("timed out", EffectKind.NONE)
        self.assertTrue(plan.retry_on_native)
        self.assertFalse(plan.must_reobserve)


class FallbackBackendTests(unittest.TestCase):
    def test_a_working_cua_call_never_touches_native(self) -> None:
        native = _FakeBackend()
        backend = FallbackComputerUseBackend(_FakeBackend(), lambda: native)
        self.assertTrue(backend.perform(CLICK).ok)
        self.assertEqual(native.performed, [])
        self.assertEqual(backend.actual_driver, "cua")

    def test_an_undelivered_call_is_redone_natively(self) -> None:
        primary = _FakeBackend([ComputerUseResult(False, action=CLICK, error="connection refused")])
        native = _FakeBackend()
        backend = FallbackComputerUseBackend(primary, lambda: native)

        result = backend.perform(CLICK)
        self.assertTrue(result.ok)
        self.assertEqual(native.performed, [CLICK], "the click never landed, so native should do it")
        self.assertEqual(backend.actual_driver, "native")

    def test_an_uncertain_call_is_not_replayed_anywhere(self) -> None:
        primary = _FakeBackend([ComputerUseResult(False, action=CLICK, error="cua_driver_failed:TimeoutExpired:...")])
        native = _FakeBackend()
        backend = FallbackComputerUseBackend(primary, lambda: native)

        result = backend.perform(CLICK)
        self.assertFalse(result.ok)
        self.assertEqual(native.performed, [], "a click that may have landed must not be repeated")
        self.assertIn("cua_delivery_uncertain", result.error)
        self.assertTrue(backend.last_plan.must_reobserve)

    def test_a_partially_executed_workflow_is_never_restarted(self) -> None:
        primary = _FakeBackend([ComputerUseResult(False, action=ComputerAction("workflow"), error="CUA workflow step failed")])
        native = _FakeBackend()
        backend = FallbackComputerUseBackend(primary, lambda: native)

        result = backend.perform_sequence([CLICK, CLICK])
        self.assertFalse(result.ok)
        self.assertEqual(native.performed, [])
        self.assertEqual(backend.last_plan.reason, "cua_sequence_partial")

    def test_observation_falls_back_freely(self) -> None:
        native = _FakeBackend()
        backend = FallbackComputerUseBackend(_FakeBackend(observe_error=RuntimeError("timed out")), lambda: native)

        self.assertEqual(backend.observe(), "observation:active_window")
        self.assertEqual(native.observed, 1)
        self.assertEqual(backend.actual_driver, "native")

    def test_native_is_not_constructed_until_it_is_needed(self) -> None:
        built: list[int] = []

        def factory():
            built.append(1)
            return _FakeBackend()

        backend = FallbackComputerUseBackend(_FakeBackend(), factory)
        backend.perform(CLICK)
        self.assertEqual(built, [], "a healthy CUA session should not spin up the native driver")

    def test_unknown_attributes_come_from_the_primary(self) -> None:
        primary = _FakeBackend()
        primary.session_id = "session-1"  # type: ignore[attr-defined]
        backend = FallbackComputerUseBackend(primary, lambda: _FakeBackend())
        self.assertEqual(backend.session_id, "session-1")

    def test_platform_checks_see_through_the_wrapper(self) -> None:
        # Callers that identify the backend by class name would otherwise stop
        # recognising a CUA session once it is wrapped, silently changing which
        # action sequence macOS gets.
        from unittest.mock import patch

        from agent_companion.core.tools.desktop_workflow import _is_macos_backend

        class CuaDriverBackend:
            pass

        class WindowsComputerUseBackend:
            pass

        with patch("agent_companion.core.tools.desktop_workflow.sys.platform", "darwin"):
            self.assertTrue(_is_macos_backend(FallbackComputerUseBackend(CuaDriverBackend(), lambda: None)))
            self.assertFalse(_is_macos_backend(FallbackComputerUseBackend(WindowsComputerUseBackend(), lambda: None)))


if __name__ == "__main__":
    unittest.main()
