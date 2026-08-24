"""An unset `${VAR}` must never be used as if it were a value.

`_expand_env` leaves the reference in place when neither the environment nor
the keyring resolves it, which is right for an API key: every `is_configured`
check reads the `${` prefix to distinguish "not set up yet" from "deliberately
blank". It is wrong for an endpoint. `mimo_tts` does
`config.tts.base_url or DEFAULT_BASE_URL`, and the literal string
`${JOI_TTS_BASE_URL}` is truthy -- so a provider that would have defaulted its
own endpoint posted to a hostname made of punctuation instead. Readiness still
reported configured, because MiMo's check asks only for a key, and the failure
surfaced much later as `tts_failed` with the cause nowhere in the message.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from agent_companion.core.config import load_app_config


CONFIG = """
app:
  title: T
llm:
  provider: openai_compatible
  use_mock: false
  base_url: ${JOI_TEST_UNSET_BASE}
  model: ${JOI_TEST_UNSET_MODEL}
  api_key: ${JOI_TEST_UNSET_KEY}
tts:
  enabled: true
  provider: mimo
  base_url: ${JOI_TEST_UNSET_BASE}
  model: mimo-v2.5-tts
  api_key: ${JOI_TEST_UNSET_KEY}
asr:
  enabled: true
  provider: mimo
  base_url: ${JOI_TEST_UNSET_BASE}
  model: mimo-v2.5-asr
  api_key: ${JOI_TEST_UNSET_KEY}
characters:
  - name: T
    setting: t
"""


class UnresolvedReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        for name in ("JOI_TEST_UNSET_BASE", "JOI_TEST_UNSET_MODEL", "JOI_TEST_UNSET_KEY"):
            os.environ.pop(name, None)
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.yaml"
        self.path.write_text(CONFIG, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_endpoints_and_models_come_back_empty_not_literal(self) -> None:
        config = load_app_config(self.path)
        for value in (config.llm.base_url, config.llm.model, config.tts.base_url, config.asr.base_url):
            self.assertEqual(value, "", "an unresolved reference must not survive as a usable value")

    def test_a_client_that_defaults_its_endpoint_now_gets_to(self) -> None:
        config = load_app_config(self.path)
        # This is the exact expression in mimo_tts; before the fix it chose the
        # placeholder over the default.
        self.assertEqual(config.tts.base_url or "https://api.xiaomimimo.com/v1", "https://api.xiaomimimo.com/v1")

    def test_a_resolved_reference_is_still_substituted(self) -> None:
        os.environ["JOI_TEST_UNSET_BASE"] = "https://example.invalid/v1"
        try:
            self.assertEqual(load_app_config(self.path).tts.base_url, "https://example.invalid/v1")
        finally:
            os.environ.pop("JOI_TEST_UNSET_BASE", None)

    def test_nothing_reports_itself_configured_on_a_blank_endpoint(self) -> None:
        config = load_app_config(self.path)
        self.assertFalse(config.asr.is_configured)


if __name__ == "__main__":
    unittest.main()
