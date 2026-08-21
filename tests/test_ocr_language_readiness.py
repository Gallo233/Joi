"""OCR reports ready only when it can read the languages it was configured for.

Tesseract complains about a language it cannot load, carries on with the ones it
can, and exits 0. Through pytesseract that reads as a success with fewer words
in it -- so a user who configured Chinese gets English recognition and nothing
says so. Readiness has to mean the configured languages are installed, not
merely that the binary runs.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_companion.core import runtime_status
from agent_companion.core.runtime_status import _absent_ocr_languages, _safe_error


class _FakePytesseract:
    class pytesseract:  # noqa: N801 - mirrors the real module's shape
        tesseract_cmd = ""

    def __init__(self, installed: list[str] | None, explode: bool = False) -> None:
        self._installed = installed
        self._explode = explode

    def get_tesseract_version(self):
        if self._explode:
            raise RuntimeError("tesseract not on PATH")
        return "5.5.3"

    def get_languages(self, config: str = "") -> list[str]:
        if self._explode:
            raise RuntimeError("tesseract not on PATH")
        return list(self._installed or [])


def _with(installed: list[str] | None, explode: bool = False):
    # The probe is cached per process, so a test that stubs it has to start from
    # an empty cache or it reads whatever the previous one left there.
    runtime_status._OCR_PROBE_CACHE.clear()
    return patch.object(runtime_status.importlib, "import_module", return_value=_FakePytesseract(installed, explode))


class AbsentOcrLanguageTests(unittest.TestCase):
    def tearDown(self) -> None:
        runtime_status._OCR_PROBE_CACHE.clear()

    def test_the_language_set_is_probed_once_rather_than_per_status_call(self) -> None:
        # `_ready_payload()` is rebuilt on every status refresh and may not shell
        # out to external tools; an uncached probe runs `tesseract` each time.
        fake = _FakePytesseract(["eng"])
        calls = []
        original = fake.get_languages
        fake.get_languages = lambda config="": (calls.append(config), original(config))[1]
        runtime_status._OCR_PROBE_CACHE.clear()
        with patch.object(runtime_status.importlib, "import_module", return_value=fake):
            for _ in range(5):
                _absent_ocr_languages("chi_sim+eng", live=True)
        self.assertEqual(len(calls), 1)

    def test_a_configured_language_that_is_not_installed_is_named(self) -> None:
        with _with(["eng", "osd"]):
            self.assertEqual(_absent_ocr_languages("chi_sim+eng+jpn", live=True), ["chi_sim", "jpn"])

    def test_nothing_is_missing_when_every_language_is_installed(self) -> None:
        with _with(["chi_sim", "eng", "jpn", "osd"]):
            self.assertEqual(_absent_ocr_languages("chi_sim+eng+jpn", live=True), [])

    def test_an_unreadable_language_list_is_not_evidence_of_a_missing_one(self) -> None:
        # Reporting OCR unavailable because the probe itself failed would be a
        # worse error than the silent degradation this exists to catch.
        with _with(None, explode=True):
            self.assertEqual(_absent_ocr_languages("chi_sim+eng", live=True), [])
        with _with([]):
            self.assertEqual(_absent_ocr_languages("chi_sim+eng", live=True), [])

    def test_an_empty_configuration_asks_for_nothing(self) -> None:
        with _with(["eng"]):
            self.assertEqual(_absent_ocr_languages("", live=True), [])
            self.assertEqual(_absent_ocr_languages("  ", live=True), [])

    def test_the_configured_string_is_split_on_its_own_separator(self) -> None:
        with _with(["eng"]):
            self.assertEqual(_absent_ocr_languages("chi_sim+eng", live=True), ["chi_sim"])
            self.assertEqual(_absent_ocr_languages("chi_sim eng", live=True), ["chi_sim"])


class OcrLanguageErrorIsSafeTests(unittest.TestCase):
    def test_the_missing_language_code_survives_sanitising(self) -> None:
        # Without an allowlist entry the code is replaced by a generic one, and
        # the advice shown to the user goes back to "install the dependencies"
        # for a machine whose dependencies are already installed.
        self.assertEqual(_safe_error("ocr_language_missing"), "ocr_language_missing")

    def test_the_code_carries_no_detail_of_its_own(self) -> None:
        # It names a capability gap. Which languages, and where the data lives,
        # stay out of a field that reaches the shell.
        for fragment in ("/", "\\", "chi_sim", "tessdata"):
            self.assertNotIn(fragment, _safe_error("ocr_language_missing"))


if __name__ == "__main__":
    unittest.main()
