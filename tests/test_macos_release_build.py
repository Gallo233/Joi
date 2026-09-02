"""What the macOS release build reads back off its own product.

Joi ships unsigned from GitHub, so the check that matters is not "is this
notarized" -- it never will be -- but "is this bundle sealed". An unsealed
bundle fails to open at all, and it looks identical to a working one in every
build log, so the distinction is asserted here rather than eyeballed.
"""

from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from build_macos_release import (  # noqa: E402
    ADHOC_IDENTITY,
    BUNDLE_IDENTIFIER,
    parse_codesign_display,
    parse_signing_identities,
    resolve_signing_identity,
    signature_problems,
)


FIND_IDENTITY_OUTPUT = """
  1) 0123456789ABCDEF0123456789ABCDEF01234567 "Apple Development: Someone (AB12CD34EF)"
  2) FEDCBA9876543210FEDCBA9876543210FEDCBA98 "Developer ID Application: Someone (AB12CD34EF)"
     2 valid identities found
"""

# What Tauri leaves behind when it is given no identity at all: the linker's
# signature on the main binary, and nothing sealing the bundle around it.
UNSEALED = """
Executable=/tmp/Joi.app/Contents/MacOS/joi-shell
Identifier=joi_shell-1ac5d3d068a30b18
CodeDirectory v=20400 size=115448 flags=0x20002(adhoc,linker-signed) hashes=3604+0
Signature=adhoc
Sealed Resources=none
"""

SEALED_ADHOC = """
Executable=/tmp/Joi.app/Contents/MacOS/joi-shell
Identifier=com.gallo233.joi
CodeDirectory v=20500 size=29041 flags=0x10002(adhoc,runtime) hashes=901+3
Signature=adhoc
Sealed Resources version=2 rules=13 files=1379
"""


class SigningIdentityTests(unittest.TestCase):
    def test_identities_are_read_out_of_the_security_listing(self) -> None:
        self.assertEqual(
            parse_signing_identities(FIND_IDENTITY_OUTPUT),
            [
                "Apple Development: Someone (AB12CD34EF)",
                "Developer ID Application: Someone (AB12CD34EF)",
            ],
        )

    def test_no_certificate_means_ad_hoc_rather_than_no_signing(self) -> None:
        identity, reason = resolve_signing_identity(None, [])
        self.assertEqual(identity, ADHOC_IDENTITY)
        self.assertIn("ad-hoc", reason)

    def test_a_developer_id_is_preferred_over_ad_hoc(self) -> None:
        identity, _ = resolve_signing_identity(None, parse_signing_identities(FIND_IDENTITY_OUTPUT))
        self.assertEqual(identity, "Developer ID Application: Someone (AB12CD34EF)")

    def test_an_explicit_environment_identity_wins(self) -> None:
        # CI imports a certificate and names it; choosing for it there would
        # turn a signed lane into an unsigned product without saying so.
        identity, _ = resolve_signing_identity(
            "Developer ID Application: CI (ZZ99YY88XX)",
            parse_signing_identities(FIND_IDENTITY_OUTPUT),
        )
        self.assertEqual(identity, "Developer ID Application: CI (ZZ99YY88XX)")


class SignatureVerificationTests(unittest.TestCase):
    def test_an_unsealed_bundle_is_rejected(self) -> None:
        problems = signature_problems(parse_codesign_display(UNSEALED))
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("sealed resources" in problem for problem in problems))
        self.assertTrue(any(BUNDLE_IDENTIFIER in problem for problem in problems))

    def test_a_sealed_ad_hoc_bundle_passes(self) -> None:
        info = parse_codesign_display(SEALED_ADHOC)
        self.assertEqual(info["identifier"], BUNDLE_IDENTIFIER)
        self.assertTrue(info["adhoc"])
        self.assertTrue(info["hardened_runtime"])
        self.assertTrue(info["sealed_resources"])
        self.assertEqual(signature_problems(info), [])


if __name__ == "__main__":
    unittest.main()
