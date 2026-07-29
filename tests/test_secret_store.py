from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_companion.core import secret_store


class _KeyringBackend:
    priority = 1

    def __init__(self, *, value: str = "", error: Exception | None = None) -> None:
        self.value = value
        self.error = error
        self.reads = 0
        self.writes = 0
        self.deletes = 0

    def get_password(self, service: str, account: str) -> str:
        self.reads += 1
        if self.error is not None:
            raise self.error
        return self.value

    def set_password(self, service: str, account: str, value: str) -> None:
        self.writes += 1
        self.value = value
        self.error = None

    def delete_password(self, service: str, account: str) -> None:
        self.deletes += 1
        self.value = ""
        self.error = None


class ManagedSecretCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        secret_store._reset_secret_cache_for_tests()

    def tearDown(self) -> None:
        secret_store._reset_secret_cache_for_tests()

    def test_denied_keychain_read_is_not_retried_in_the_same_process(self) -> None:
        backend = _KeyringBackend(error=RuntimeError("denied"))
        with patch.object(secret_store, "_keyring_backend", return_value=backend):
            self.assertEqual(secret_store.managed_secret(secret_store.LLM_API_KEY_ENV), "")
            self.assertEqual(secret_store.managed_secret(secret_store.LLM_API_KEY_ENV), "")
            status = secret_store.managed_secret_status(secret_store.LLM_API_KEY_ENV)

        self.assertEqual(backend.reads, 1)
        self.assertFalse(status["stored"])
        self.assertFalse(status["secure_store_available"])

    def test_successful_read_is_cached_without_exposing_it_in_status(self) -> None:
        backend = _KeyringBackend(value="secret-from-keychain")
        with patch.object(secret_store, "_keyring_backend", return_value=backend):
            self.assertEqual(secret_store.managed_secret(secret_store.LLM_API_KEY_ENV), "secret-from-keychain")
            status = secret_store.managed_secret_status(secret_store.LLM_API_KEY_ENV)

        self.assertEqual(backend.reads, 1)
        self.assertEqual(status, {"stored": True, "source": "system", "secure_store_available": True})
        self.assertNotIn("secret-from-keychain", str(status))

    def test_store_and_delete_refresh_the_session_cache(self) -> None:
        backend = _KeyringBackend(error=RuntimeError("denied"))
        with patch.object(secret_store, "_keyring_backend", return_value=backend):
            self.assertEqual(secret_store.managed_secret(secret_store.LLM_API_KEY_ENV), "")
            self.assertEqual(
                secret_store.store_managed_secret(secret_store.LLM_API_KEY_ENV, "new-secret-value"),
                (True, ""),
            )
            self.assertEqual(secret_store.managed_secret(secret_store.LLM_API_KEY_ENV), "new-secret-value")
            self.assertEqual(secret_store.delete_managed_secret(secret_store.LLM_API_KEY_ENV), (True, ""))
            self.assertEqual(secret_store.managed_secret(secret_store.LLM_API_KEY_ENV), "")

        self.assertEqual(backend.reads, 1)
        self.assertEqual(backend.writes, 1)
        self.assertEqual(backend.deletes, 1)


if __name__ == "__main__":
    unittest.main()
