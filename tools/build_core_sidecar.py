from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys


def _host_target() -> str:
    try:
        return subprocess.check_output(
            ["rustc", "--print", "host-tuple"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        output = subprocess.check_output(["rustc", "-vV"], text=True)
        for line in output.splitlines():
            if line.startswith("host:"):
                return line.split(":", 1)[1].strip()
        raise RuntimeError("unable to determine Rust host target")


def _data_argument(source: Path, destination: str) -> str:
    return f"{source}{os.pathsep}{destination}"


def build_sidecar(workspace: Path, *, target: str = "", if_missing: bool = False) -> Path:
    workspace = workspace.resolve()
    host_target = _host_target()
    target = target.strip() or host_target
    if target != host_target:
        raise RuntimeError(
            f"PyInstaller cannot cross-compile Joi Core: requested {target}, host is {host_target}"
        )
    extension = ".exe" if sys.platform == "win32" else ""
    destination = (
        workspace
        / "agent_companion"
        / "shell"
        / "src-tauri"
        / "binaries"
        / f"joi-core-{target}{extension}"
    )
    if if_missing and destination.is_file():
        print(f"Joi Core sidecar already exists: {destination}")
        return destination

    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "PyInstaller is required. Install requirements-build.txt with the Python used for this build."
        ) from exc

    entry = workspace / "agent_companion" / "core" / "sidecar_entry.py"
    target_root = workspace / "agent_companion" / "shell" / "src-tauri" / "target" / "sidecar"
    dist_dir = target_root / "dist"
    build_dir = target_root / "build"
    spec_dir = target_root / "spec"
    for path in (dist_dir, build_dir, spec_dir, destination.parent):
        path.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "joi-core",
        "--paths",
        str(workspace),
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(spec_dir),
        "--collect-submodules",
        "keyring.backends",
        "--collect-submodules",
        "websockets",
        "--exclude-module",
        "PySide6",
    ]
    seed_paths = (
        (workspace / "config.example.yaml", "."),
        (workspace / "agent_companion" / "config", "agent_companion/config"),
        (workspace / "agent_companion" / "skills", "agent_companion/skills"),
        (workspace / "agent_companion" / "adapters", "agent_companion/adapters"),
        (workspace / "agent_companion" / "web_widget" / "assets", "agent_companion/web_widget/assets"),
        (workspace / "agent_companion" / "shell" / "public" / "live2d" / "joi", "agent_companion/shell/public/live2d/joi"),
    )
    for source, bundled_path in seed_paths:
        if source.exists():
            command.extend(["--add-data", _data_argument(source, bundled_path)])
    command.append(str(entry))
    subprocess.run(command, cwd=workspace, check=True)

    built = dist_dir / f"joi-core{extension}"
    if not built.is_file():
        raise RuntimeError(f"PyInstaller completed without producing {built}")
    shutil.copy2(built, destination)
    destination.chmod(destination.stat().st_mode | 0o111)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    print(f"Built {destination.name} ({destination.stat().st_size} bytes, sha256={digest})")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the standalone Joi Core Tauri sidecar.")
    parser.add_argument("--workspace", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--target", default="")
    parser.add_argument("--if-missing", action="store_true")
    args = parser.parse_args()
    try:
        build_sidecar(Path(args.workspace), target=args.target, if_missing=args.if_missing)
    except Exception as exc:
        print(f"Joi Core sidecar build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
