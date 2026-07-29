"""The one place Joi talks to an OpenAI-compatible provider.

Every call site used to resolve an endpoint, build a client, fire a request and
swallow whatever came back with a bare `except Exception`. That works until you
need to answer a question about it: how long did it take, which provider
answered, was the user still waiting, what exactly did we send. None of those
had an answer, and the error text -- which routinely echoes the endpoint and
the key -- was the only thing left.

Routing through here gives each call a manifest, a budget, a cancellation
check and a recorded outcome, without call sites learning anything about
vendors (TDD §10.1).
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Sequence

from agent_companion.core.model_call import (
    CallBudget,
    CallOutcome,
    DataManifest,
    ModelCallLedger,
    build_manifest,
    execute_call,
)


# Per-route defaults. Planning must not keep the user waiting the way a long
# reasoning answer legitimately can.
ROUTE_BUDGETS: dict[str, CallBudget] = {
    "fast": CallBudget(timeout_ms=30_000, max_fallbacks=2),
    "reasoning": CallBudget(timeout_ms=45_000, max_fallbacks=2),
    "vision": CallBudget(timeout_ms=45_000, max_fallbacks=1, allow_categories=("text", "screen")),
    "code": CallBudget(timeout_ms=60_000, max_fallbacks=1),
    "summarize": CallBudget(timeout_ms=30_000, max_fallbacks=1),
    "voice_style": CallBudget(timeout_ms=8_000, max_fallbacks=0, allow_categories=("text",)),
}

# Planning is on the critical path of every message; it gets a short leash.
PLANNER_BUDGET = CallBudget(timeout_ms=8_000, max_fallbacks=1, allow_categories=("text",))

_LEDGER_LOCK = threading.Lock()
_DEFAULT_LEDGER: ModelCallLedger | None = None


def default_ledger() -> ModelCallLedger:
    """Process-wide ledger, so diagnostics can see calls from every component.

    Injecting one through every constructor would be cleaner in isolation but
    would leave most call sites unrecorded until each was threaded through.
    """
    global _DEFAULT_LEDGER
    with _LEDGER_LOCK:
        if _DEFAULT_LEDGER is None:
            _DEFAULT_LEDGER = ModelCallLedger()
        return _DEFAULT_LEDGER


def budget_for(route: str) -> CallBudget:
    return ROUTE_BUDGETS.get(route, CallBudget())


def resolve_endpoints(llm_config: Any, route: str) -> list[Any]:
    """Every endpoint that may serve this route, primary first."""
    try:
        from agent_companion.core.config import ModelRouter

        router = ModelRouter(llm_config)
        endpoints = list(router.resolve_with_fallback(route) or [])
    except Exception:
        endpoints = []
    if endpoints:
        return [endpoint for endpoint in endpoints if getattr(endpoint, "model", "")]
    try:
        single = ModelRouter(llm_config).resolve(route)
    except Exception:
        return []
    return [single] if getattr(single, "model", "") else []


def chat_completion(
    llm_config: Any,
    route: str,
    messages: Sequence[dict[str, Any]],
    *,
    manifest: DataManifest | None = None,
    budget: CallBudget | None = None,
    ledger: ModelCallLedger | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    temperature: float | None = None,
    response_format: dict[str, Any] | None = None,
    screenshots: int = 0,
    instructions: str | None = None,
    user_input: str = "",
) -> CallOutcome:
    """Ask a route for a completion, within budget, and record the attempt.

    `instructions`/`user_input` let a caller opt into OpenAI's responses API on
    providers that support it while still supplying `messages` for everyone
    else, so the choice of wire format stays inside this module.
    """
    active_budget = budget or budget_for(route)
    payload_text = "".join(str(message.get("content") or "") for message in messages)
    call_manifest = manifest or build_manifest(route, text=payload_text, screenshots=screenshots)

    try:
        from openai import OpenAI
    except Exception:
        # No SDK is an unconfigured capability, not a provider failure.
        return execute_call(route, [], lambda endpoint: None, manifest=call_manifest, budget=active_budget, ledger=ledger or default_ledger())

    def invoke(endpoint: Any) -> str:
        client = OpenAI(
            api_key=getattr(endpoint, "api_key", "") or "ollama",
            base_url=getattr(endpoint, "base_url", "") or "",
            timeout=max(1.0, active_budget.timeout_ms / 1000.0),
        )
        # Which wire format a provider prefers is a provider detail, so it is
        # decided here rather than branched on at every call site.
        if getattr(endpoint, "provider", "") == "openai" and instructions is not None:
            response = client.responses.create(
                model=getattr(endpoint, "model", ""),
                instructions=instructions,
                input=user_input or "",
                max_output_tokens=active_budget.output_budget or 600,
                store=False,
            )
            return str(getattr(response, "output_text", "") or "")
        request: dict[str, Any] = {"model": getattr(endpoint, "model", ""), "messages": list(messages)}
        if temperature is not None:
            request["temperature"] = temperature
        if response_format is not None:
            request["response_format"] = response_format
        if active_budget.output_budget:
            request["max_tokens"] = active_budget.output_budget
        response = client.chat.completions.create(**request)
        return response.choices[0].message.content or ""

    return execute_call(
        route,
        resolve_endpoints(llm_config, route),
        invoke,
        manifest=call_manifest,
        budget=active_budget,
        ledger=ledger or default_ledger(),
        is_cancelled=is_cancelled,
    )
