"""Model calls stay inside their budget and never carry secrets into records."""

from __future__ import annotations

import unittest

from agent_companion.core.model_call import (
    DATA_CATEGORIES,
    ROUTE_LABELS,
    CallBudget,
    DataManifest,
    DataManifestEntry,
    ModelCallLedger,
    build_manifest,
    degradation_notice,
    execute_call,
    provider_ref,
    redact,
)


class _Endpoint:
    def __init__(self, provider: str, base_url: str = "https://api.example.com/v1", api_key: str = "sk-secret-value-123456") -> None:
        self.provider = provider
        self.base_url = base_url
        self.api_key = api_key


class _Clock:
    """Deterministic monotonic clock in seconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RouteContractTests(unittest.TestCase):
    def test_route_labels_are_the_stable_business_vocabulary(self) -> None:
        self.assertEqual(set(ROUTE_LABELS), {"fast", "reasoning", "vision", "code", "summarize", "voice_style"})

    def test_data_categories_are_fixed(self) -> None:
        self.assertEqual(set(DATA_CATEGORIES), {"text", "file", "screen", "audio", "code"})


class RedactionTests(unittest.TestCase):
    def test_keys_endpoints_and_paths_never_survive(self) -> None:
        for secret in (
            "sk-abcdef1234567890",
            "Authorization: Bearer abcdef123456",
            "api_key=abcdef123456",
            "https://api.openai.com/v1/chat",
            "/Users/someone/models/llama.gguf",
            r"C:\Users\someone\model.bin",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, redact(f"provider said: {secret}"))
                self.assertIn("[redacted]", redact(f"provider said: {secret}"))

    def test_provider_ref_identifies_without_exposing(self) -> None:
        reference = provider_ref(_Endpoint("openai"), 1)
        self.assertEqual(reference, "openai#1")
        self.assertNotIn("api.example.com", reference)
        self.assertNotIn("sk-", reference)


class ManifestTests(unittest.TestCase):
    def test_a_manifest_describes_categories_not_content(self) -> None:
        manifest = build_manifest("vision", text="看看这个窗口", screenshots=2)
        self.assertEqual(manifest.categories, ("screen", "text"))
        payload = manifest.payload()
        self.assertNotIn("看看这个窗口", str(payload))
        self.assertGreater(payload["total_bytes"], 0)

    def test_a_budget_refuses_categories_the_user_did_not_allow(self) -> None:
        budget = CallBudget(allow_categories=("text",))
        allowed, reason = budget.permits(build_manifest("vision", text="hi", screenshots=1))
        self.assertFalse(allowed)
        self.assertEqual(reason, "category_not_allowed:screen")

    def test_a_budget_refuses_an_oversized_payload(self) -> None:
        budget = CallBudget(input_budget=10)
        allowed, reason = budget.permits(build_manifest("fast", text="x" * 100))
        self.assertFalse(allowed)
        self.assertEqual(reason, "input_budget_exceeded")

    def test_an_unknown_category_is_refused_rather_than_sent(self) -> None:
        manifest = DataManifest("fast", (DataManifestEntry("telemetry", 1),))
        allowed, reason = CallBudget().permits(manifest)
        self.assertFalse(allowed)
        self.assertEqual(reason, "unknown_data_category:telemetry")


class ExecuteCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = ModelCallLedger()
        self.clock = _Clock()

    def test_a_successful_call_records_one_attempt(self) -> None:
        outcome = execute_call("fast", [_Endpoint("openai")], lambda endpoint: "answer", ledger=self.ledger, clock=self.clock)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.value, "answer")
        self.assertEqual(len(outcome.records), 1)
        self.assertEqual(outcome.records[0].fallback_index, 0)

    def test_a_failing_provider_falls_through_to_the_next(self) -> None:
        calls: list[str] = []

        def invoke(endpoint):
            calls.append(endpoint.provider)
            if endpoint.provider == "primary":
                raise RuntimeError("upstream 503 at https://api.example.com with sk-secret-value-123456")
            return "answer"

        outcome = execute_call("fast", [_Endpoint("primary"), _Endpoint("backup")], invoke, ledger=self.ledger, clock=self.clock)
        self.assertTrue(outcome.ok)
        self.assertEqual(calls, ["primary", "backup"])
        self.assertEqual(outcome.records[-1].fallback_index, 1)

    def test_a_provider_error_message_is_never_stored(self) -> None:
        def invoke(endpoint):
            raise RuntimeError("upstream rejected key sk-secret-value-123456 at https://api.example.com")

        outcome = execute_call("fast", [_Endpoint("primary")], invoke, ledger=self.ledger, clock=self.clock)
        self.assertFalse(outcome.ok)
        recorded = str(self.ledger.records())
        self.assertNotIn("sk-secret-value-123456", recorded)
        self.assertNotIn("api.example.com", recorded)
        # Only the exception type survives, which is enough to diagnose.
        self.assertEqual(outcome.records[0].error_code, "RuntimeError")

    def test_the_fallback_chain_is_bounded(self) -> None:
        attempts: list[str] = []

        def invoke(endpoint):
            attempts.append(endpoint.provider)
            raise RuntimeError("down")

        endpoints = [_Endpoint(f"p{index}") for index in range(6)]
        outcome = execute_call("fast", endpoints, invoke, budget=CallBudget(max_fallbacks=2), ledger=self.ledger, clock=self.clock)
        self.assertFalse(outcome.ok)
        self.assertEqual(len(attempts), 3, "one primary plus max_fallbacks")
        self.assertEqual(outcome.error_code, "all_providers_failed")

    def test_cancellation_stops_before_the_next_provider(self) -> None:
        attempts: list[str] = []
        cancelled = {"value": False}

        def invoke(endpoint):
            attempts.append(endpoint.provider)
            cancelled["value"] = True  # the user cancels while this one runs
            raise RuntimeError("down")

        outcome = execute_call(
            "fast",
            [_Endpoint("primary"), _Endpoint("backup")],
            invoke,
            ledger=self.ledger,
            is_cancelled=lambda: cancelled["value"],
            clock=self.clock,
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "cancelled")
        self.assertEqual(attempts, ["primary"], "a cancelled call must not start another provider")
        self.assertEqual(outcome.records[-1].cancellation_reason, "user_cancelled")

    def test_the_timeout_covers_the_whole_chain_not_each_attempt(self) -> None:
        def invoke(endpoint):
            self.clock.advance(20.0)
            raise RuntimeError("slow")

        outcome = execute_call(
            "fast",
            [_Endpoint("a"), _Endpoint("b"), _Endpoint("c")],
            invoke,
            budget=CallBudget(timeout_ms=30_000, max_fallbacks=5),
            ledger=self.ledger,
            clock=self.clock,
        )
        self.assertEqual(outcome.status, "timeout")
        self.assertEqual(outcome.records[-1].error_code, "budget_timeout")

    def test_an_unconfigured_route_is_unavailable_not_an_error(self) -> None:
        outcome = execute_call("vision", [], lambda endpoint: None, ledger=self.ledger, clock=self.clock)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.status, "unavailable")
        self.assertEqual(outcome.error_code, "route_not_configured")
        self.assertTrue(outcome.degraded)

    def test_a_refused_payload_never_reaches_a_provider(self) -> None:
        attempts: list[str] = []
        outcome = execute_call(
            "vision",
            [_Endpoint("openai")],
            lambda endpoint: attempts.append("called"),
            manifest=build_manifest("vision", screenshots=1),
            budget=CallBudget(allow_categories=("text",)),
            ledger=self.ledger,
            clock=self.clock,
        )
        self.assertEqual(attempts, [])
        self.assertEqual(outcome.status, "refused")
        self.assertEqual(outcome.error_code, "category_not_allowed:screen")


class LedgerProjectionTests(unittest.TestCase):
    def test_route_health_reports_counts_without_identities(self) -> None:
        ledger = ModelCallLedger()
        clock = _Clock()
        execute_call("fast", [_Endpoint("openai")], lambda e: "ok", ledger=ledger, clock=clock)
        execute_call("fast", [_Endpoint("openai")], lambda e: (_ for _ in ()).throw(RuntimeError("x")), ledger=ledger, clock=clock)
        execute_call("vision", [], lambda e: None, ledger=ledger, clock=clock)

        health = ledger.route_health()
        self.assertEqual(health["fast"]["calls"], 2)
        self.assertEqual(health["fast"]["succeeded"], 1)
        self.assertEqual(health["fast"]["degraded"], 1)
        self.assertEqual(health["vision"]["degraded"], 1)
        self.assertNotIn("api.example.com", str(health))
        self.assertNotIn("sk-", str(health))

    def test_the_ledger_is_bounded(self) -> None:
        ledger = ModelCallLedger(limit=10)
        for _ in range(50):
            execute_call("fast", [_Endpoint("openai")], lambda e: "ok", ledger=ledger)
        self.assertLessEqual(len(ledger.records()), 10)


class DegradationTests(unittest.TestCase):
    def test_rule_guidance_is_never_counted_as_an_ai_conversation(self) -> None:
        notice = degradation_notice("fast", "unavailable")
        self.assertTrue(notice["degraded"])
        self.assertFalse(notice["counts_as_ai_conversation"])

    def test_deterministic_observation_is_not_passed_off_as_vision(self) -> None:
        notice = degradation_notice("vision", "unavailable")
        self.assertIn("不是视觉模型", notice["message"])


if __name__ == "__main__":
    unittest.main()
