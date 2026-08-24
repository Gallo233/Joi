"""Fail-closed usage limits for the anonymous web experience.

The desktop product never configures this module.  A web Core gets one
``GuestLimits`` instance from the broker and therefore keeps the normal Joi
runtime single-tenant while sharing only an aggregate, lock-protected daily
ledger with sibling Core processes.  The ledger contains counters, never
prompts, audio, credentials, IP addresses, or provider responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable

try:  # Linux/macOS deployment path.  Windows desktop never enables the ledger.
    import fcntl
except ImportError:  # pragma: no cover - defensive Windows fallback
    fcntl = None  # type: ignore[assignment]


@dataclass(frozen=True)
class TokenReservation:
    amount: int


class GuestLimits:
    def __init__(
        self,
        *,
        session_token_limit: int = 0,
        daily_token_limit: int = 0,
        realtime_session_seconds: int = 0,
        realtime_total_seconds: int = 0,
        daily_realtime_seconds: int = 0,
        ledger_path: Path | None = None,
        usage_path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.session_token_limit = max(0, int(session_token_limit))
        self.daily_token_limit = max(0, int(daily_token_limit))
        self.realtime_session_seconds = max(0, int(realtime_session_seconds))
        self.realtime_total_seconds = max(0, int(realtime_total_seconds))
        self.daily_realtime_seconds = max(0, int(daily_realtime_seconds))
        self.ledger_path = ledger_path.expanduser().resolve() if ledger_path else None
        self.usage_path = usage_path.expanduser().resolve() if usage_path else None
        self._clock = clock
        self._lock = threading.RLock()
        self._tokens_used = 0
        self._realtime_seconds_used = 0
        self._active_realtime: dict[str, tuple[float, int]] = {}
        self._write_usage()

    @property
    def enabled(self) -> bool:
        return any((
            self.session_token_limit,
            self.daily_token_limit,
            self.realtime_session_seconds,
            self.realtime_total_seconds,
            self.daily_realtime_seconds,
        ))

    def reserve_tokens(self, amount: int) -> TokenReservation | None:
        amount = max(1, int(amount))
        with self._lock:
            if self.session_token_limit and self._tokens_used + amount > self.session_token_limit:
                return None
            accepted = self._change_daily("tokens", amount, self.daily_token_limit)
            if not accepted:
                return None
            self._tokens_used += amount
            self._write_usage()
            return TokenReservation(amount)

    def settle_tokens(self, reservation: TokenReservation, actual: int, *, failed: bool = False) -> None:
        # Provider failures may still be billed and frequently omit usage. Keep
        # the full reservation in that case instead of guessing optimistically.
        if failed:
            return
        # `actual <= 0` is the caller stating that nothing was charged at all --
        # a request that never reached a provider. That is a release, not a
        # settlement, and must return the whole reservation; rounding it up to
        # one token was the difference between a refund and a no-op here.
        if int(actual) <= 0:
            with self._lock:
                self._tokens_used = max(0, self._tokens_used - reservation.amount)
                try:
                    self._change_daily("tokens", -reservation.amount, 0)
                except (OSError, ValueError):
                    pass
                self._write_usage()
            return
        used = max(1, int(actual))
        if used > reservation.amount:
            # A provider should respect its output cap, but usage is the source
            # of truth if it does not. Record the overage so every later call
            # fails closed instead of silently under-counting paid usage.
            overage = used - reservation.amount
            with self._lock:
                self._tokens_used += overage
                try:
                    self._change_daily("tokens", overage, 0)
                except (OSError, ValueError):
                    pass
                self._write_usage()
            return
        refund = reservation.amount - used
        if not refund:
            return
        with self._lock:
            self._tokens_used = max(0, self._tokens_used - refund)
            try:
                self._change_daily("tokens", -refund, 0)
            except (OSError, ValueError):
                # Keep the shared ledger conservative; settlement diagnostics
                # must never turn a completed provider answer into an error.
                pass
            self._write_usage()

    def reserve_realtime(self, owner_id: str, requested_seconds: int) -> dict[str, Any]:
        owner = str(owner_id or "")
        if not owner:
            return {"ok": False, "error": "realtime_owner_required"}
        with self._lock:
            # The full and compact iframes are two WebSocket owners attached to
            # one visitor Core. Only one of them may hold the paid microphone.
            if self._active_realtime:
                return {"ok": False, "error": "realtime_session_already_active"}
            requested = max(0, int(requested_seconds)) or self.realtime_session_seconds
            if self.realtime_session_seconds:
                requested = min(requested, self.realtime_session_seconds)
            remaining = self.realtime_total_seconds - self._realtime_seconds_used if self.realtime_total_seconds else requested
            allowed = max(0, min(requested, remaining))
            if allowed <= 0:
                return {"ok": False, "error": "guest_realtime_budget_exceeded"}
            if not self._change_daily("realtime_seconds", allowed, self.daily_realtime_seconds):
                return {"ok": False, "error": "guest_realtime_daily_budget_exceeded"}
            self._active_realtime[owner] = (self._clock(), allowed)
            self._write_usage()
            return {"ok": True, "max_seconds": allowed}

    def finish_realtime(self, owner_id: str) -> None:
        with self._lock:
            active = self._active_realtime.pop(str(owner_id or ""), None)
            if active is None:
                return
            started, reserved = active
            actual = max(1, min(reserved, int(math.ceil(self._clock() - started))))
            self._realtime_seconds_used += actual
            refund = reserved - actual
            if refund:
                try:
                    self._change_daily("realtime_seconds", -refund, 0)
                except (OSError, ValueError):
                    pass
            self._write_usage()

    def realtime_remaining_seconds(self) -> int:
        with self._lock:
            if not self.realtime_total_seconds:
                return self.realtime_session_seconds
            return max(0, self.realtime_total_seconds - self._realtime_seconds_used)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "date": self._today(),
                "tokens_used": self._tokens_used,
                "token_limit": self.session_token_limit,
                "realtime_seconds_used": self._realtime_seconds_used,
                "realtime_total_seconds": self.realtime_total_seconds,
                "realtime_active": bool(self._active_realtime),
                "updated_at": self._clock(),
            }

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock(), timezone.utc).date().isoformat()

    def _change_daily(self, field: str, delta: int, limit: int) -> bool:
        path = self.ledger_path
        if path is None:
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                try:
                    payload = json.load(handle)
                except (json.JSONDecodeError, ValueError):
                    payload = {}
                if not isinstance(payload, dict) or payload.get("date") != self._today():
                    payload = {"date": self._today(), "tokens": 0, "realtime_seconds": 0}
                current = max(0, int(payload.get(field) or 0))
                if delta > 0 and limit and current + delta > limit:
                    return False
                payload[field] = max(0, current + delta)
                payload["updated_at"] = self._clock()
                handle.seek(0)
                handle.truncate()
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                return True
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _write_usage(self) -> None:
        path = self.usage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(self.snapshot(), ensure_ascii=False, sort_keys=True), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            return


_ACTIVE_LOCK = threading.Lock()
_ACTIVE: GuestLimits | None = None


def configure_guest_limits(limits: GuestLimits | None) -> None:
    global _ACTIVE
    with _ACTIVE_LOCK:
        _ACTIVE = limits if limits is not None and limits.enabled else None


def active_guest_limits() -> GuestLimits | None:
    with _ACTIVE_LOCK:
        return _ACTIVE


def estimate_tokens(value: Any) -> int:
    return max(1, int(math.ceil(len(str(value or "").encode("utf-8")) / 4)))
