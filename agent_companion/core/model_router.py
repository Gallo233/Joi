"""Enhanced Model Router with multi-provider support.

Supports:
- Named route labels: fast, reasoning, vision, code, summarize, voice_style
- Multiple providers: OpenAI, OpenRouter, Ollama, DeepSeek, etc.
- Fallback chains: try primary, fall back to secondary
- Latency tracking per route

All providers use OpenAI-compatible API format, so no provider-specific code needed.
Just configure different base_url + api_key + model per route.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelEndpoint:
    base_url: str
    model: str
    api_key: str = ""
    provider: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7


@dataclass
class RouteLatency:
    """Tracks latency stats for a route."""
    total_calls: int = 0
    total_ms: float = 0.0
    last_ms: float = 0.0
    failures: int = 0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.total_calls if self.total_calls > 0 else 0.0

    def record(self, ms: float, success: bool = True) -> None:
        self.total_calls += 1
        self.total_ms += ms
        self.last_ms = ms
        if not success:
            self.failures += 1


# Default route configuration
DEFAULT_ROUTES: dict[str, dict[str, str]] = {
    "fast": {"description": "快速响应，低延迟"},
    "reasoning": {"description": "深度推理，高质量"},
    "vision": {"description": "视觉理解"},
    "code": {"description": "代码生成"},
    "summarize": {"description": "摘要生成"},
    "voice_style": {"description": "语音风格"},
}


class ModelRouter:
    """Routes model requests to appropriate providers based on task type.

    Backward compatible with the original single-provider LlmConfig.
    """

    def __init__(self, llm: Any = None) -> None:
        self._llm = llm
        self._routes: dict[str, list[ModelEndpoint]] = {}
        self._latency: dict[str, RouteLatency] = {}
        self._setup_default_routes()

    def _setup_default_routes(self) -> None:
        """Set up default routes from LlmConfig."""
        if self._llm is None:
            return

        # Primary text endpoint
        primary = ModelEndpoint(
            base_url=self._llm.base_url,
            model=self._llm.model,
            api_key=self._llm.api_key,
            provider=getattr(self._llm, 'provider', 'openai'),
        )

        # All routes default to primary
        for route in DEFAULT_ROUTES:
            self._routes[route] = [primary]

        # Vision override
        if getattr(self._llm, 'is_vision_configured', False):
            self._routes["vision"] = [ModelEndpoint(
                base_url=self._llm.vision_base_url or self._llm.base_url,
                model=self._llm.vision_model or self._llm.model,
                api_key=self._llm.vision_api_key or self._llm.api_key,
                provider="vision",
            )]

        # Expression override
        if getattr(self._llm, 'is_expression_configured', False):
            self._routes["voice_style"] = [ModelEndpoint(
                base_url=self._llm.expression_base_url or self._llm.base_url,
                model=self._llm.expression_model or self._llm.model,
                api_key=self._llm.expression_api_key or self._llm.api_key,
                provider="expression",
            )]

    def resolve(self, use: str = "text") -> ModelEndpoint:
        """Resolve a route label to a ModelEndpoint.

        Args:
            use: Route label - "text", "vision", "expression", or any custom label.

        Returns:
            ModelEndpoint with base_url, model, api_key.
        """
        # Map legacy names to route labels
        route_map = {
            "text": "fast",
            "vision": "vision",
            "expression": "voice_style",
        }
        route = route_map.get(use, use)

        endpoints = self._routes.get(route, [])
        if endpoints:
            return endpoints[0]

        # Fallback: use LlmConfig directly
        if self._llm:
            return ModelEndpoint(
                base_url=self._llm.base_url,
                model=self._llm.model,
                api_key=self._llm.api_key,
            )
        return ModelEndpoint(base_url="", model="", api_key="")

    def resolve_with_fallback(self, use: str = "text") -> list[ModelEndpoint]:
        """Resolve a route with all fallback endpoints."""
        route_map = {"text": "fast", "vision": "vision", "expression": "voice_style"}
        route = route_map.get(use, use)
        return self._routes.get(route, [self.resolve(use)])

    def add_route(self, label: str, endpoint: ModelEndpoint, primary: bool = True) -> None:
        """Add or update a route with a new endpoint."""
        if primary or label not in self._routes:
            self._routes[label] = [endpoint] + self._routes.get(label, [])
        else:
            self._routes.setdefault(label, []).append(endpoint)

    def record_latency(self, route: str, ms: float, success: bool = True) -> None:
        """Record latency for a route call."""
        if route not in self._latency:
            self._latency[route] = RouteLatency()
        self._latency[route].record(ms, success)

    def latency_stats(self) -> dict[str, dict[str, float]]:
        """Get latency stats for all routes."""
        return {
            route: {
                "avg_ms": round(stats.avg_ms, 1),
                "total_calls": stats.total_calls,
                "failures": stats.failures,
                "last_ms": round(stats.last_ms, 1),
            }
            for route, stats in self._latency.items()
        }

    def available_routes(self) -> list[dict[str, Any]]:
        """List all configured routes with their endpoints."""
        result = []
        for label, endpoints in sorted(self._routes.items()):
            info = DEFAULT_ROUTES.get(label, {})
            result.append({
                "label": label,
                "description": info.get("description", ""),
                "primary_model": endpoints[0].model if endpoints else "",
                "fallback_count": max(0, len(endpoints) - 1),
            })
        return result
