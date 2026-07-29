"""The call sites really go through the budgeted path.

A layer that exists but is not on the path protects nothing, and looks
identical to one that is. These tests drive the actual chat and expression
code and assert the call was recorded, bounded and stripped of identities.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_companion.core.model_call import CallOutcome, ModelCallLedger
from agent_companion.core.provider_client import PLANNER_BUDGET, ROUTE_BUDGETS, budget_for, chat_completion, resolve_endpoints


class _Endpoint:
    def __init__(self, provider: str = "openai_compatible", model: str = "test-model") -> None:
        self.provider = provider
        self.model = model
        self.base_url = "https://api.example.com/v1"
        self.api_key = "sk-secret-value-abcdef"


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = type("Msg", (), {"content": content})()


class _FakeOpenAI:
    """Stands in for the SDK, recording what it was asked for."""

    calls: list[dict] = []
    failure: Exception | None = None

    def __init__(self, **kwargs) -> None:
        type(self).calls.append({"init": kwargs})
        self.chat = type("Chat", (), {"completions": self})()
        self.responses = self

    def create(self, **kwargs):
        type(self).calls.append(kwargs)
        if type(self).failure is not None:
            raise type(self).failure
        return type("Resp", (), {"choices": [_Choice('{"reply":"好的","voice_text":"好的"}')], "output_text": '{"reply":"好的"}'})()


class _FakeCharacter:
    """Enough of a character for the chat tool to build its prompt."""

    name = "Joi"
    setting = "测试用角色设定"
    sprites = [{"id": "1", "emotion": "neutral"}]

    def voice_text_lang(self, fallback: str = "zh") -> str:
        return fallback


def _configured_config():
    config = type("Cfg", (), {})()
    config.llm = type("Llm", (), {"is_configured": True, "use_mock": False, "temperature": 0.5})()
    config.tts = type("Tts", (), {"text_lang": "zh"})()
    config.primary_character = _FakeCharacter()
    config.characters = [config.primary_character]
    return config


class RouteBudgetTests(unittest.TestCase):
    def test_every_route_has_a_bounded_budget(self) -> None:
        for route, budget in ROUTE_BUDGETS.items():
            with self.subTest(route=route):
                self.assertGreater(budget.timeout_ms, 0)
                self.assertGreaterEqual(budget.max_fallbacks, 0)

    def test_planning_gets_a_shorter_leash_than_reasoning(self) -> None:
        # Planning is on the critical path of every message.
        self.assertLess(PLANNER_BUDGET.timeout_ms, budget_for("reasoning").timeout_ms)

    def test_voice_style_may_only_send_text(self) -> None:
        self.assertEqual(budget_for("voice_style").allow_categories, ("text",))

    def test_vision_may_send_screens_but_not_audio(self) -> None:
        allowed = budget_for("vision").allow_categories
        self.assertIn("screen", allowed)
        self.assertNotIn("audio", allowed)


class ResolveEndpointTests(unittest.TestCase):
    def test_an_unconfigured_route_resolves_to_nothing(self) -> None:
        self.assertEqual(resolve_endpoints(None, "fast"), [])

    def test_endpoints_without_a_model_are_dropped(self) -> None:
        class Router:
            def resolve_with_fallback(self, route):
                return [_Endpoint(model=""), _Endpoint(model="real")]

        with patch("agent_companion.core.config.ModelRouter", lambda config: Router()):
            resolved = resolve_endpoints(object(), "fast")
        self.assertEqual([endpoint.model for endpoint in resolved], ["real"])


class ChatCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeOpenAI.calls = []
        _FakeOpenAI.failure = None
        self.ledger = ModelCallLedger()

    def _run(self, **kwargs) -> CallOutcome:
        module = type("M", (), {"OpenAI": _FakeOpenAI})
        with patch.dict("sys.modules", {"openai": module}), patch(
            "agent_companion.core.provider_client.resolve_endpoints", return_value=[_Endpoint()]
        ):
            return chat_completion(object(), "fast", [{"role": "user", "content": "你好"}], ledger=self.ledger, **kwargs)

    def test_a_call_is_recorded_with_a_manifest(self) -> None:
        outcome = self._run()
        self.assertTrue(outcome.ok)
        records = self.ledger.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["manifest"]["categories"], ["text"])
        self.assertGreater(records[0]["manifest"]["total_bytes"], 0)

    def test_the_prompt_is_not_stored_in_the_record(self) -> None:
        self._run()
        self.assertNotIn("你好", str(self.ledger.records()))

    def test_the_timeout_is_handed_to_the_sdk(self) -> None:
        self._run()
        init = next(call["init"] for call in _FakeOpenAI.calls if "init" in call)
        self.assertAlmostEqual(init["timeout"], budget_for("fast").timeout_ms / 1000.0)

    def test_a_provider_failure_records_only_its_type(self) -> None:
        _FakeOpenAI.failure = RuntimeError("bad key sk-secret-value-abcdef at https://api.example.com")
        outcome = self._run()
        self.assertFalse(outcome.ok)
        recorded = str(self.ledger.records())
        self.assertNotIn("sk-secret-value-abcdef", recorded)
        self.assertNotIn("api.example.com", recorded)
        self.assertEqual(outcome.records[0].error_code, "RuntimeError")

    def test_a_missing_sdk_is_reported_as_unavailable(self) -> None:
        with patch.dict("sys.modules", {"openai": None}), patch(
            "agent_companion.core.provider_client.resolve_endpoints", return_value=[_Endpoint()]
        ):
            outcome = chat_completion(object(), "fast", [{"role": "user", "content": "hi"}], ledger=self.ledger)
        self.assertEqual(outcome.status, "unavailable")


class ChatToolGoesThroughTheLayerTests(unittest.TestCase):
    """The main conversation path, not just the helper."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_the_chat_tool_calls_the_budgeted_helper(self) -> None:
        from agent_companion.core.schemas import ToolRequest
        from agent_companion.core.tools.chat import CompanionChatTool

        seen: list[str] = []

        def fake_completion(llm_config, route, messages, **kwargs):
            seen.append(route)
            return CallOutcome(True, '{"reply":"好的","voice_text":"好的","emotion":"neutral"}', "succeeded")

        tool = CompanionChatTool(self.workspace)
        config = _configured_config()

        # _config is resolved once in __init__, so replace the resolved value.
        tool._config = config
        with patch("agent_companion.core.tools.chat.chat_completion", fake_completion):
            result = tool.run(ToolRequest("companion.chat", {"text": "你好"}))

        self.assertEqual(seen, ["fast"], "chat must go through the budgeted helper")
        self.assertTrue(result.ok)

    def test_a_refused_call_becomes_an_explained_error(self) -> None:
        from agent_companion.core.schemas import ToolRequest
        from agent_companion.core.tools.chat import CompanionChatTool

        def refused(llm_config, route, messages, **kwargs):
            return CallOutcome(False, None, "refused", "category_not_allowed:screen")

        tool = CompanionChatTool(self.workspace)
        config = _configured_config()

        tool._config = config
        with patch("agent_companion.core.tools.chat.chat_completion", refused):
            result = tool.run(ToolRequest("companion.chat", {"text": "你好"}))

        self.assertEqual(result.agent_state.get("model_error"), "model_request_refused")
        self.assertFalse(result.ok)
        # The user is told the request was blocked, not which internal category
        # tripped it.
        self.assertNotIn("category_not_allowed", result.display_card.summary)


if __name__ == "__main__":
    unittest.main()
