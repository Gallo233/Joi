from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.import_qwen_realtime_secret import import_qwen_api_key, read_qwen_api_key


class QwenSecretImportTests(unittest.TestCase):
    def test_reads_vertical_console_export_without_returning_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key.csv"
            path.write_text("apiKey,sk-private-test-only\napiHost,https://example.invalid\nworkspaceId,private-id\n", encoding="utf-8")
            self.assertEqual(read_qwen_api_key(path), "sk-private-test-only")

    def test_import_stores_only_managed_secret_and_protects_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key.csv"
            path.write_text("apiKey,sk-private-test-only\n", encoding="utf-8")
            with patch("tools.import_qwen_realtime_secret.store_managed_secret", return_value=(True, "")) as store:
                ok, error = import_qwen_api_key(path)
            self.assertTrue(ok)
            self.assertEqual(error, "")
            store.assert_called_once_with("JOI_QWEN_REALTIME_API_KEY", "sk-private-test-only")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_export_never_calls_secure_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key.csv"
            path.write_text("apiHost,https://example.invalid\n", encoding="utf-8")
            with patch("tools.import_qwen_realtime_secret.store_managed_secret") as store:
                self.assertEqual(import_qwen_api_key(path), (False, "qwen_key_csv_invalid"))
            store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
