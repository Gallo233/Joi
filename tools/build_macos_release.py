"""Build the macOS release bundle and prove what its signature actually is.

Joi is distributed from GitHub rather than the App Store, so a build is allowed
to be unsigned -- but it is not allowed to be *unsealed*. Tauri skips signing
entirely when it is given no identity, leaving the linker's ad-hoc signature on
the main binary alone: no sealed resources, and a code identifier that is not
even the bundle identifier. macOS reports a bundle in that state as damaged
rather than as unverified, which reads to a user as a broken download instead of
an install step.

Passing an explicit ad-hoc identity is what closes that gap. This script picks
the strongest identity available -- a Developer ID when the machine has one,
ad-hoc otherwise -- runs the release build with it, and then reads the signature
back off the product instead of trusting that the build did what was asked.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


BUNDLE_IDENTIFIER = "com.gallo233.joi"
DEFAULT_TARGET = "aarch64-apple-darwin"
ADHOC_IDENTITY = "-"


def parse_signing_identities(output: str) -> list[str]:
    """Return the identity names in `security find-identity -v -p codesigning`."""

    identities = []
    for line in output.splitlines():
        match = re.search(r'^\s*\d+\)\s+[0-9A-F]{40}\s+"(.+)"\s*$', line)
        if match:
            identities.append(match.group(1))
    return identities


def resolve_signing_identity(env_identity: str | None, identities: list[str]) -> tuple[str, str]:
    """Choose the identity to build with, and say why it was chosen.

    An explicit environment variable always wins: it is how CI passes an
    imported certificate, and second-guessing it would make a signed lane
    silently produce an unsigned product.
    """

    if env_identity:
        return env_identity, "APPLE_SIGNING_IDENTITY was set"
    developer_ids = [name for name in identities if name.startswith("Developer ID Application:")]
    if developer_ids:
        return developer_ids[0], "found a Developer ID Application certificate on this machine"
    return ADHOC_IDENTITY, "no Developer ID certificate on this machine, signing ad-hoc"


def parse_codesign_display(output: str) -> dict[str, object]:
    """Read the fields of `codesign -dv` that decide whether a bundle installs."""

    identifier = None
    flags = ""
    sealed = False
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Identifier="):
            identifier = line.split("=", 1)[1]
        elif line.startswith("CodeDirectory "):
            match = re.search(r"flags=0x[0-9a-f]+\(([^)]*)\)", line)
            flags = match.group(1) if match else ""
        elif line.startswith("Sealed Resources"):
            sealed = "none" not in line
    return {
        "identifier": identifier,
        "adhoc": "adhoc" in flags,
        "hardened_runtime": "runtime" in flags,
        "sealed_resources": sealed,
    }


def signature_problems(info: dict[str, object]) -> list[str]:
    """Name the states that make a downloaded bundle fail to open at all."""

    problems = []
    if info.get("identifier") != BUNDLE_IDENTIFIER:
        problems.append(
            f"code identifier is {info.get('identifier')!r}, expected {BUNDLE_IDENTIFIER!r} -- "
            "the bundle was left with the linker's signature instead of being signed"
        )
    if not info.get("sealed_resources"):
        problems.append(
            "the bundle has no sealed resources -- macOS reports this as a damaged app, "
            "not as an unverified one"
        )
    return problems


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(command)}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument(
        "--live2d-source",
        default=None,
        help="Directory holding the licensed public/ asset tree. Defaults to whatever "
        "sync-live2d-assets.mjs can find on this machine.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Skip the build and only read the signature off the existing bundle.",
    )
    args = parser.parse_args()

    if sys.platform != "darwin":
        raise SystemExit("This release build only runs on macOS.")

    repo_root = Path(__file__).resolve().parent.parent
    shell_dir = repo_root / "agent_companion" / "shell"
    bundle_dir = shell_dir / "src-tauri" / "target" / args.target / "release" / "bundle"
    app_path = bundle_dir / "macos" / "Joi.app"

    env = dict(os.environ)
    identity, reason = resolve_signing_identity(
        env.get("APPLE_SIGNING_IDENTITY"),
        parse_signing_identities(_run(["security", "find-identity", "-v", "-p", "codesigning"])),
    )
    env["APPLE_SIGNING_IDENTITY"] = identity
    if args.live2d_source:
        env["JOI_LIVE2D_SOURCE"] = str(Path(args.live2d_source).expanduser().resolve())

    print(f"Signing identity: {identity}  ({reason})")

    if not args.verify_only:
        if shutil.which("npm") is None:
            raise SystemExit("npm is required to build the shell.")
        print("Building... (this runs the fail-closed release gate: Core sidecar, pinned assets)")
        subprocess.run(
            ["npm", "run", "tauri", "--", "build", "--target", args.target],
            cwd=shell_dir,
            env=env,
            check=True,
        )

    if not app_path.is_dir():
        raise SystemExit(f"No bundle at {app_path}")

    info = parse_codesign_display(
        subprocess.run(
            ["codesign", "-dv", "--verbose=2", str(app_path)],
            capture_output=True,
            text=True,
            check=False,
        ).stderr
    )
    problems = signature_problems(info)
    if problems:
        for problem in problems:
            sys.stderr.write(f"[FAIL] {problem}\n")
        return 1

    _run(["codesign", "--verify", "--deep", "--strict", str(app_path)])

    print(f"[OK] signature seals {info['identifier']}, resources sealed", end="")
    print(", hardened runtime" if info["hardened_runtime"] else "")

    dmgs = sorted((bundle_dir / "dmg").glob("*.dmg"))
    for dmg in dmgs:
        print(f"\nDMG      {dmg}")
        print(f"size     {dmg.stat().st_size:,} bytes")
        print(f"sha256   {sha256_of(dmg)}")

    if info["adhoc"]:
        print(
            "\nThis build is ad-hoc signed and not notarized, which is the chosen\n"
            "distribution model. Gatekeeper will refuse the first open, so every\n"
            "release that carries it must point at docs/INSTALL_MACOS.md."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
