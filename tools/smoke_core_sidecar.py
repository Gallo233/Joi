from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


def _host_target() -> str:
    try:
        return subprocess.check_output(
            ["rustc", "--print", "host-tuple"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        output = subprocess.check_output(["rustc", "-vV"], text=True)
        return next(line.split(":", 1)[1].strip() for line in output.splitlines() if line.startswith("host:"))


def _sidecar_path(workspace: Path) -> Path:
    extension = ".exe" if sys.platform == "win32" else ""
    return (
        workspace
        / "agent_companion"
        / "shell"
        / "src-tauri"
        / "binaries"
        / "joi-core-runtime"
        / f"joi-core{extension}"
    )


def _port_pair() -> tuple[int, int]:
    for _ in range(32):
        first = socket.socket()
        first.bind(("127.0.0.1", 0))
        port = int(first.getsockname()[1])
        if port >= 65535:
            first.close()
            continue
        second = socket.socket()
        try:
            second.bind(("127.0.0.1", port + 1))
        except OSError:
            first.close()
            second.close()
            continue
        first.close()
        second.close()
        return port, port + 1
    raise RuntimeError("unable_to_allocate_port_pair")


async def _check_websocket(port: int, token: str, instance_id: str) -> None:
    import websockets

    encoded = urllib.parse.quote(token, safe="")
    async with websockets.connect(f"ws://127.0.0.1:{port}/?token={encoded}") as websocket:
        message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
    payload = message.get("params") if isinstance(message, dict) else {}
    if payload.get("product") != "joi-core" or payload.get("instance_id") != instance_id:
        raise RuntimeError("core_identity_mismatch")
    model_url = str((payload.get("character") or {}).get("model_url") or "")
    if model_url and f"/characters/{encoded}/" not in model_url:
        raise RuntimeError("character_asset_token_missing")

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/?token=wrong") as websocket:
            await asyncio.wait_for(websocket.recv(), timeout=5)
    except websockets.exceptions.ConnectionClosed as error:
        if error.code != 4401:
            raise RuntimeError(f"unexpected_auth_close:{error.code}") from error
    else:
        raise RuntimeError("invalid_token_accepted")


def run_smoke(workspace: Path, binary: Path | None = None) -> float:
    sidecar = (binary or _sidecar_path(workspace)).resolve()
    if not sidecar.is_file():
        raise RuntimeError("sidecar_missing")
    port, asset_port = _port_pair()
    token = secrets.token_hex(32)
    instance_id = f"smoke-{secrets.token_hex(8)}"
    with tempfile.TemporaryDirectory(prefix="joi-sidecar-smoke-") as directory:
        data_home = Path(directory)
        ready_file = data_home / "ready.json"
        environment = dict(os.environ)
        environment["JOI_DATA_HOME"] = str(data_home)
        environment["JOI_CORE_SESSION_TOKEN"] = token
        started_at = time.monotonic()
        process = subprocess.Popen(
            [
                str(sidecar),
                "--workspace",
                str(data_home),
                "--serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--instance-id",
                instance_id,
                "--ready-file",
                str(ready_file),
            ],
            cwd=data_home,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline and not ready_file.is_file():
                if process.poll() is not None:
                    raise RuntimeError("sidecar_exited_before_ready")
                time.sleep(0.1)
            if not ready_file.is_file():
                raise RuntimeError("sidecar_ready_timeout")
            startup_seconds = time.monotonic() - started_at
            ready = json.loads(ready_file.read_text(encoding="utf-8"))
            if ready.get("instance_id") != instance_id or ready.get("port") != port:
                raise RuntimeError("ready_file_identity_mismatch")
            with urllib.request.urlopen(f"http://127.0.0.1:{asset_port}/livez", timeout=5) as response:
                live = json.load(response)
            if live.get("product") != "joi-core" or live.get("instance_id") != instance_id:
                raise RuntimeError("livez_identity_mismatch")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{asset_port}/readyz", timeout=5)
            except urllib.error.HTTPError as error:
                if error.code != 401:
                    raise
            else:
                raise RuntimeError("readyz_missing_auth")
            asyncio.run(_check_websocket(port, token, instance_id))
            return startup_seconds
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the standalone Joi Core sidecar.")
    parser.add_argument("--workspace", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--binary", default="")
    args = parser.parse_args()
    try:
        startup_seconds = run_smoke(
            Path(args.workspace).resolve(),
            Path(args.binary) if args.binary else None,
        )
    except Exception as error:
        print(f"Joi Core sidecar smoke failed: {type(error).__name__}:{error}", file=sys.stderr)
        return 1
    print(f"Joi Core sidecar smoke: OK (ready in {startup_seconds:.3f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
