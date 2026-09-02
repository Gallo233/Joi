from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agent_companion.core.guest_limits import GuestLimits, configure_guest_limits
from agent_companion.core.model_call import CallBudget, build_manifest, execute_call


class _Clock:
    def __init__(self, value: float = 1_800_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class GuestLimitTests(unittest.TestCase):
    def tearDown(self) -> None:
        configure_guest_limits(None)

    def test_model_call_reserves_before_provider_and_settles_to_reported_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            limits = GuestLimits(
                session_token_limit=100,
                daily_token_limit=100,
                ledger_path=Path(directory) / "ledger.json",
            )
            configure_guest_limits(limits)

            class Answer(str):
                input_tokens = 7
                output_tokens = 5

            outcome = execute_call(
                "fast",
                [object()],
                lambda _endpoint: Answer("answer"),
                manifest=build_manifest("fast", text="hello"),
                budget=CallBudget(output_budget=20, max_fallbacks=0),
            )

            self.assertTrue(outcome.ok)
            self.assertEqual(limits.snapshot()["tokens_used"], 12)

    def test_a_call_that_cannot_be_reserved_never_reaches_provider(self) -> None:
        limits = GuestLimits(session_token_limit=10)
        configure_guest_limits(limits)
        called = False

        def invoke(_endpoint: object) -> str:
            nonlocal called
            called = True
            return "should not happen"

        outcome = execute_call(
            "fast",
            [object()],
            invoke,
            manifest=build_manifest("fast", text="hello"),
            budget=CallBudget(output_budget=20, max_fallbacks=0),
        )
        self.assertFalse(called)
        self.assertEqual(outcome.error_code, "guest_token_budget_exceeded")

    def test_daily_tokens_are_shared_across_core_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            first = GuestLimits(session_token_limit=100, daily_token_limit=20, ledger_path=ledger)
            second = GuestLimits(session_token_limit=100, daily_token_limit=20, ledger_path=ledger)
            self.assertIsNotNone(first.reserve_tokens(12))
            self.assertIsNone(second.reserve_tokens(9))

    def test_reported_provider_overage_is_not_hidden_by_the_reservation(self) -> None:
        limits = GuestLimits(session_token_limit=25)
        reservation = limits.reserve_tokens(10)
        self.assertIsNotNone(reservation)
        assert reservation is not None
        limits.settle_tokens(reservation, 30)
        self.assertEqual(limits.snapshot()["tokens_used"], 30)
        self.assertIsNone(limits.reserve_tokens(1))

    def test_realtime_is_single_owner_and_refunds_unused_reserved_seconds(self) -> None:
        clock = _Clock()
        with tempfile.TemporaryDirectory() as directory:
            limits = GuestLimits(
                realtime_session_seconds=30,
                realtime_total_seconds=40,
                daily_realtime_seconds=40,
                ledger_path=Path(directory) / "ledger.json",
                clock=clock,
            )
            first = limits.reserve_realtime("full", 30)
            self.assertEqual(first, {"ok": True, "max_seconds": 30})
            self.assertEqual(limits.reserve_realtime("compact", 30)["error"], "realtime_session_already_active")
            clock.value += 7.1
            limits.finish_realtime("full")
            self.assertEqual(limits.snapshot()["realtime_seconds_used"], 8)
            self.assertEqual(limits.reserve_realtime("compact", 30)["max_seconds"], 30)


if __name__ == "__main__":
    unittest.main()


class GuestBudgetFailureTests(unittest.TestCase):
    """A blip that never reached a provider must not be billed to the visitor.

    `execute_call` walks its fallback endpoints, reserving before each attempt.
    Keeping the reservation on every failure meant one unreachable endpoint
    could spend a whole session's budget across the fallback chain without a
    single token being generated -- and the visitor was then told their
    conversation allowance was used up.
    """

    def tearDown(self) -> None:
        configure_guest_limits(None)

    def _limits(self, directory: str) -> GuestLimits:
        limits = GuestLimits(session_token_limit=10_000, ledger_path=Path(directory) / "ledger.json")
        configure_guest_limits(limits)
        return limits

    def _run(self, error: Exception) -> int:
        def invoke(_endpoint):
            raise error

        execute_call(
            "fast",
            [object()],
            invoke,
            manifest=build_manifest("fast", text="hello"),
            budget=CallBudget(output_budget=100, max_fallbacks=0),
        )
        return 0

    def test_unreachable_provider_refunds_the_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            limits = self._limits(directory)
            connection_error = type("APIConnectionError", (Exception,), {})()
            self._run(connection_error)
            self.assertEqual(limits.snapshot()["tokens_used"], 0)

    def test_a_provider_that_answered_with_an_error_still_costs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            limits = self._limits(directory)
            status_error = type("BadRequestError", (Exception,), {})()
            self._run(status_error)
            self.assertGreater(limits.snapshot()["tokens_used"], 0)
