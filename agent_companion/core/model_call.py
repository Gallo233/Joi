"""What Joi sends to a model provider, what it costs, and what it may say about it.

Three separate concerns live here because each has bitten differently:

- **A manifest before the call.** The user agreed to a provider seeing certain
  kinds of data. Deciding that at the call site, buried in a prompt builder,
  makes it impossible to check. The manifest states the categories and sizes up
  front so it can be shown, tested, and refused.
- **A bounded call.** Timeouts, cancellation and fallback all have to end. An
  unbounded fallback chain turns one dead provider into a long silence, and a
  call that ignores cancellation keeps spending after the user moved on.
- **A safe projection.** Latency and status are useful; API keys, endpoints,
  local model paths and raw provider errors are not. The record keeps the
  former and never stores the latter.

See TDD §10.1 and PRD-AIM-003.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import threading
import time
from typing import Any, Callable, Iterable, Sequence
import uuid


# The stable business labels. Business code asks for a capability, never for a
# vendor, so switching providers is configuration rather than a code change.
ROUTE_LABELS = ("fast", "reasoning", "vision", "code", "summarize", "voice_style")

# What a payload may contain. Anything not declared here cannot be sent.
DATA_CATEGORIES = ("text", "file", "screen", "audio", "code")

CALL_STATUSES = ("succeeded", "timeout", "cancelled", "provider_error", "unavailable", "refused")

# Terminal states for a run that could not reach a model at all.
DEGRADED_STATUSES = frozenset({"unavailable", "timeout", "provider_error"})

DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_MAX_FALLBACKS = 2

# Values that must never reach a record, a log or the shell.
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:api[_-]?key|authorization|bearer|token|password)\b\s*[:=]?\s*\S+", re.IGNORECASE),
    re.compile(r"https?://\S+"),
    re.compile(r"(?:[A-Za-z]:\\|/(?:Users|home|private|var|Volumes)/)\S*"),
)


def redact(text: Any, limit: int = 200) -> str:
    """Strip anything that identifies a credential, endpoint or local path."""
    value = str(text or "")
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("[redacted]", value)
    return " ".join(value.split())[:limit]


@dataclass(frozen=True)
class DataManifestEntry:
    category: str
    count: int = 1
    bytes: int = 0
    note: str = ""

    def payload(self) -> dict[str, Any]:
        return {"category": self.category, "count": self.count, "bytes": self.bytes, "note": redact(self.note, 80)}


@dataclass(frozen=True)
class DataManifest:
    """Declares what leaves the machine, by category rather than by content."""

    route: str
    entries: tuple[DataManifestEntry, ...] = ()
    leaves_device: bool = True

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(sorted({entry.category for entry in self.entries}))

    @property
    def total_bytes(self) -> int:
        return sum(entry.bytes for entry in self.entries)

    def unsupported(self) -> tuple[str, ...]:
        return tuple(sorted({entry.category for entry in self.entries if entry.category not in DATA_CATEGORIES}))

    def payload(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "categories": list(self.categories),
            "entries": [entry.payload() for entry in self.entries],
            "total_bytes": self.total_bytes,
            "leaves_device": self.leaves_device,
        }


def build_manifest(route: str, *, text: str = "", files: Iterable[Any] = (), screenshots: int = 0, audio_bytes: int = 0, leaves_device: bool = True) -> DataManifest:
    """Describe a payload without keeping any of it."""
    entries: list[DataManifestEntry] = []
    if text:
        entries.append(DataManifestEntry("text", 1, len(str(text).encode("utf-8"))))
    file_list = list(files)
    if file_list:
        entries.append(DataManifestEntry("file", len(file_list)))
    if screenshots:
        entries.append(DataManifestEntry("screen", int(screenshots)))
    if audio_bytes:
        entries.append(DataManifestEntry("audio", 1, int(audio_bytes)))
    return DataManifest(route=route, entries=tuple(entries), leaves_device=leaves_device)


@dataclass(frozen=True)
class CallBudget:
    """Hard limits on one logical model call, including its fallbacks."""

    timeout_ms: int = DEFAULT_TIMEOUT_MS
    input_budget: int = 0
    output_budget: int = 0
    max_fallbacks: int = DEFAULT_MAX_FALLBACKS
    allow_categories: tuple[str, ...] = DATA_CATEGORIES

    def permits(self, manifest: DataManifest) -> tuple[bool, str]:
        """Whether this payload may be sent at all."""
        unsupported = manifest.unsupported()
        if unsupported:
            return False, f"unknown_data_category:{unsupported[0]}"
        disallowed = [category for category in manifest.categories if category not in self.allow_categories]
        if disallowed:
            # The user allowed some kinds of data to leave, not all of them.
            return False, f"category_not_allowed:{disallowed[0]}"
        if self.input_budget and manifest.total_bytes > self.input_budget:
            return False, "input_budget_exceeded"
        return True, ""

    def payload(self) -> dict[str, Any]:
        return {
            "timeout_ms": self.timeout_ms,
            "input_budget": self.input_budget,
            "output_budget": self.output_budget,
            "max_fallbacks": self.max_fallbacks,
            "allow_categories": list(self.allow_categories),
        }


@dataclass
class ModelCallRecord:
    """One attempt, in the shape TDD §10.1 asks for -- and nothing more."""

    route: str
    request_id: str
    provider_ref: str = ""
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    input_budget: int = 0
    output_budget: int = 0
    elapsed_ms: float = 0.0
    status: str = "succeeded"
    fallback_index: int = 0
    cancellation_reason: str = ""
    error_code: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def payload(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "request_id": self.request_id,
            "provider_ref": self.provider_ref,
            "timeout_ms": self.timeout_ms,
            "input_budget": self.input_budget,
            "output_budget": self.output_budget,
            "elapsed_ms": round(float(self.elapsed_ms), 1),
            "status": self.status,
            "fallback_index": self.fallback_index,
            "cancellation_reason": self.cancellation_reason,
            "error_code": self.error_code,
            "manifest": self.manifest,
            "created_at": self.created_at,
        }


def provider_ref(endpoint: Any, index: int = 0) -> str:
    """A stable handle for a provider that is not its endpoint or key.

    Diagnostics need to distinguish "the second provider failed" from "the
    first did", without publishing where either one lives.
    """
    provider = str(getattr(endpoint, "provider", "") or "").strip().casefold() or "provider"
    return f"{re.sub(r'[^a-z0-9_-]', '', provider) or 'provider'}#{index}"


class ModelCallLedger:
    """Recent call records, already safe to show."""

    def __init__(self, limit: int = 200) -> None:
        self._lock = threading.RLock()
        self._limit = max(1, int(limit))
        self._records: list[ModelCallRecord] = []

    def record(self, entry: ModelCallRecord) -> ModelCallRecord:
        with self._lock:
            self._records.append(entry)
            self._records = self._records[-self._limit :]
        return entry

    def records(self, route: str = "") -> list[dict[str, Any]]:
        with self._lock:
            rows = [entry for entry in self._records if not route or entry.route == route]
        return [entry.payload() for entry in rows]

    def route_health(self) -> dict[str, dict[str, Any]]:
        """Per-route status projection: counts and latency, never identities."""
        summary: dict[str, dict[str, Any]] = {}
        with self._lock:
            rows = list(self._records)
        for entry in rows:
            bucket = summary.setdefault(entry.route, {"calls": 0, "succeeded": 0, "degraded": 0, "cancelled": 0, "total_ms": 0.0, "fallbacks_used": 0})
            bucket["calls"] += 1
            bucket["total_ms"] += float(entry.elapsed_ms)
            bucket["fallbacks_used"] += int(bool(entry.fallback_index))
            if entry.status == "succeeded":
                bucket["succeeded"] += 1
            elif entry.status == "cancelled":
                bucket["cancelled"] += 1
            elif entry.status in DEGRADED_STATUSES:
                bucket["degraded"] += 1
        for bucket in summary.values():
            bucket["avg_ms"] = round(bucket["total_ms"] / bucket["calls"], 1) if bucket["calls"] else 0.0
            bucket.pop("total_ms")
        return summary


@dataclass(frozen=True)
class CallOutcome:
    ok: bool
    value: Any = None
    status: str = "succeeded"
    error_code: str = ""
    records: tuple[ModelCallRecord, ...] = ()

    @property
    def degraded(self) -> bool:
        return self.status in DEGRADED_STATUSES

    def payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "error_code": self.error_code,
            "degraded": self.degraded,
            "attempts": [record.payload() for record in self.records],
        }


def execute_call(
    route: str,
    endpoints: Sequence[Any],
    invoke: Callable[[Any], Any],
    *,
    manifest: DataManifest | None = None,
    budget: CallBudget | None = None,
    ledger: ModelCallLedger | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> CallOutcome:
    """Run one logical model call across its fallback chain, within budget.

    `invoke` performs the provider request for a single endpoint and raises on
    failure. Cancellation is checked between attempts rather than mid-request:
    a request already in flight cannot be unsent, but the next one need not
    start.
    """
    budget = budget or CallBudget()
    manifest = manifest or DataManifest(route=route)
    request_id = f"req-{uuid.uuid4().hex[:12]}"
    records: list[ModelCallRecord] = []

    def note(**values: Any) -> ModelCallRecord:
        entry = ModelCallRecord(
            route=route,
            request_id=request_id,
            timeout_ms=budget.timeout_ms,
            input_budget=budget.input_budget,
            output_budget=budget.output_budget,
            manifest=manifest.payload(),
            **values,
        )
        records.append(entry)
        if ledger is not None:
            ledger.record(entry)
        return entry

    allowed, refusal = budget.permits(manifest)
    if not allowed:
        note(status="refused", error_code=refusal)
        return CallOutcome(False, None, "refused", refusal, tuple(records))

    if not endpoints:
        # Not an error to hide: the user has not configured this capability.
        note(status="unavailable", error_code="route_not_configured")
        return CallOutcome(False, None, "unavailable", "route_not_configured", tuple(records))

    started = clock()
    for index, endpoint in enumerate(endpoints[: max(1, budget.max_fallbacks + 1)]):
        if is_cancelled is not None and is_cancelled():
            note(status="cancelled", fallback_index=index, provider_ref=provider_ref(endpoint, index), cancellation_reason="user_cancelled", elapsed_ms=(clock() - started) * 1000)
            return CallOutcome(False, None, "cancelled", "user_cancelled", tuple(records))
        elapsed_ms = (clock() - started) * 1000
        if elapsed_ms >= budget.timeout_ms:
            note(status="timeout", fallback_index=index, provider_ref=provider_ref(endpoint, index), elapsed_ms=elapsed_ms, error_code="budget_timeout")
            return CallOutcome(False, None, "timeout", "budget_timeout", tuple(records))
        attempt_started = clock()
        try:
            value = invoke(endpoint)
        except Exception as exc:
            note(
                status="provider_error",
                fallback_index=index,
                provider_ref=provider_ref(endpoint, index),
                elapsed_ms=(clock() - attempt_started) * 1000,
                # Only the exception type; provider messages routinely echo the
                # request, the endpoint, or the key.
                error_code=type(exc).__name__,
            )
            continue
        note(status="succeeded", fallback_index=index, provider_ref=provider_ref(endpoint, index), elapsed_ms=(clock() - attempt_started) * 1000)
        return CallOutcome(True, value, "succeeded", "", tuple(records))

    return CallOutcome(False, None, "provider_error", "all_providers_failed", tuple(records))


def degradation_notice(route: str, status: str) -> dict[str, Any]:
    """What the user is told when a capability is not available.

    Naming the deterministic substitute matters: Accessibility and OCR really
    do observe the screen, but they are not a vision model understanding it,
    and saying so keeps the difference honest (TDD §10.1).
    """
    substitutes = {
        "vision": "已改用 Accessibility 与 OCR 的确定性观察，但那不是视觉模型的理解。",
        "voice_style": "已改用确定性表达，语气不会随情境变化。",
        "fast": "没有可用的文本模型，只能做设置引导和本地操作，不能算一次成功的 AI 对话。",
        "reasoning": "没有可用的推理模型，复杂任务需要你先配置 provider。",
        "code": "没有可用的代码模型。",
        "summarize": "没有可用的摘要模型。",
    }
    return {
        "route": route,
        "status": status,
        "degraded": status in DEGRADED_STATUSES,
        "message": substitutes.get(route, "这个能力当前不可用。"),
        # Kept separate on purpose: rule-based guidance is not an AI conversation.
        "counts_as_ai_conversation": False,
    }
