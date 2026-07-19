from __future__ import annotations

import os
from typing import Any


SERVICE_NAME = "Joi BYOK"
LLM_API_KEY_ACCOUNT = "llm.api_key"
LLM_API_KEY_ENV = "JOI_LLM_API_KEY"


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
    try:
        return str(backend.get_password(SERVICE_NAME, account) or "").strip()
    except Exception:
        return ""


def managed_secret_status(name: str) -> dict[str, Any]:
    if os.environ.get(name, "").strip():
        return {"stored": True, "source": "environment", "secure_store_available": True}
    account = _account_for_name(name)
    backend = _keyring_backend()
    if not account or backend is None:
        return {"stored": False, "source": "missing", "secure_store_available": False}
    try:
        stored = bool(backend.get_password(SERVICE_NAME, account))
    except Exception:
        return {"stored": False, "source": "missing", "secure_store_available": False}
    return {"stored": stored, "source": "system" if stored else "missing", "secure_store_available": True}


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
        return False, "secure_store_failed"
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
            return False, "secure_store_failed"
    return True, ""


def _account_for_name(name: str) -> str:
    return LLM_API_KEY_ACCOUNT if name == LLM_API_KEY_ENV else ""


def _keyring_backend() -> Any | None:
    try:
        import keyring

        backend = keyring.get_keyring()
        priority = float(getattr(backend, "priority", 0) or 0)
        return backend if priority > 0 else None
    except Exception:
        return None
