from __future__ import annotations

import os
from threading import RLock
from typing import Any


SERVICE_NAME = "Joi BYOK"
LLM_API_KEY_ACCOUNT = "llm.api_key"
LLM_API_KEY_ENV = "JOI_LLM_API_KEY"
QWEN_REALTIME_API_KEY_ACCOUNT = "realtime_voice.qwen_api_key"
QWEN_REALTIME_API_KEY_ENV = "JOI_QWEN_REALTIME_API_KEY"
_SECRET_CACHE: dict[str, str] = {}
_SECRET_READ_BLOCKED: set[str] = set()
_SECRET_CACHE_LOCK = RLock()


def managed_secret(name: str) -> str:
    """Resolve a managed secret without ever exposing it in status payloads."""
    env_value = os.environ.get(name, "").strip()
    if env_value:
        return env_value
    account = _account_for_name(name)
    if not account:
        return ""
    backend = _keyring_backend()
    if backend is None:
        return ""
    return _read_managed_secret(name, account, backend)


def managed_secret_status(name: str) -> dict[str, Any]:
    if os.environ.get(name, "").strip():
        return {"stored": True, "source": "environment", "secure_store_available": True}
    account = _account_for_name(name)
    backend = _keyring_backend()
    if not account or backend is None:
        return {"stored": False, "source": "missing", "secure_store_available": False}
    secret = _read_managed_secret(name, account, backend)
    with _SECRET_CACHE_LOCK:
        blocked = name in _SECRET_READ_BLOCKED
    if blocked:
        return {"stored": False, "source": "missing", "secure_store_available": False}
    return {"stored": bool(secret), "source": "system" if secret else "missing", "secure_store_available": True}


def store_managed_secret(name: str, value: str) -> tuple[bool, str]:
    account = _account_for_name(name)
    secret = str(value or "").strip()
    if not account:
        return False, "unsupported_secret"
    if len(secret) < 8 or len(secret) > 4096 or any(char in secret for char in ("\n", "\r", "\0")):
        return False, "invalid_api_key"
    backend = _keyring_backend()
    if backend is None:
        return False, "secure_store_unavailable"
    try:
        backend.set_password(SERVICE_NAME, account, secret)
    except Exception:
        _block_secret_reads(name)
        return False, "secure_store_failed"
    with _SECRET_CACHE_LOCK:
        _SECRET_CACHE[name] = secret
        _SECRET_READ_BLOCKED.discard(name)
    return True, ""


def delete_managed_secret(name: str) -> tuple[bool, str]:
    account = _account_for_name(name)
    backend = _keyring_backend()
    if not account:
        return False, "unsupported_secret"
    if backend is None:
        return False, "secure_store_unavailable"
    try:
        backend.delete_password(SERVICE_NAME, account)
    except Exception as exc:
        if type(exc).__name__ != "PasswordDeleteError":
            _block_secret_reads(name)
            return False, "secure_store_failed"
    with _SECRET_CACHE_LOCK:
        _SECRET_CACHE[name] = ""
        _SECRET_READ_BLOCKED.discard(name)
    return True, ""


def _account_for_name(name: str) -> str:
    return {
        LLM_API_KEY_ENV: LLM_API_KEY_ACCOUNT,
        QWEN_REALTIME_API_KEY_ENV: QWEN_REALTIME_API_KEY_ACCOUNT,
    }.get(name, "")


def _keyring_backend() -> Any | None:
    try:
        import keyring

        backend = keyring.get_keyring()
        priority = float(getattr(backend, "priority", 0) or 0)
        return backend if priority > 0 else None
    except Exception:
        return None


def _read_managed_secret(name: str, account: str, backend: Any) -> str:
    """Read a keychain item at most once after the user or OS denies access."""
    with _SECRET_CACHE_LOCK:
        if name in _SECRET_READ_BLOCKED:
            return ""
        if name in _SECRET_CACHE:
            return _SECRET_CACHE[name]
        try:
            secret = str(backend.get_password(SERVICE_NAME, account) or "").strip()
        except Exception:
            _SECRET_READ_BLOCKED.add(name)
            return ""
        _SECRET_CACHE[name] = secret
        return secret


def _block_secret_reads(name: str) -> None:
    with _SECRET_CACHE_LOCK:
        _SECRET_CACHE.pop(name, None)
        _SECRET_READ_BLOCKED.add(name)


def _reset_secret_cache_for_tests() -> None:
    with _SECRET_CACHE_LOCK:
        _SECRET_CACHE.clear()
        _SECRET_READ_BLOCKED.clear()
