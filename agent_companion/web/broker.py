"""Session broker and dynamic reverse proxy for the anonymous Joi web demo.

Caddy terminates TLS and forwards ``/session``, ``/ws/*`` and ``/assets/*``
to this loopback-only service.  Every visitor gets one Core process and one
workspace; the full and compact iframes share its token and therefore receive
the same Core broadcasts.

Only Python's standard library is used so the deployment has no second web
framework or dependency graph to patch.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import http.server
import ipaddress
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import urllib.parse

try:
    import fcntl
except ImportError:  # pragma: no cover - deployment target is Linux
    fcntl = None  # type: ignore[assignment]


PUBLIC_RPC_METHODS = (
    "user.message",
    "approval.resolve",
    "conversation.history",
    "character.list",
    "character.activate",
    "character.detail",
    "memory.list",
    "memory.pending",
    "thread.*",
    "project.list",
    "voice.transcribe",
    "voice.cancel",
    "voice.realtime.session.start",
    "voice.realtime.audio.append",
    "voice.realtime.session.stop",
    "voice.realtime.session.status",
    "core.ping",
    "core.livez",
    "core.readyz",
)

# A character package may hold a model far larger than any page asset:
# `CharacterPackageManager` already accepts members up to 128MB and validates
# every one of them before a package is installed, so that is the honest ceiling
# here rather than a second, tighter number invented at the proxy.
MAX_ASSET_BYTES = 128 * 1024 * 1024

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}


@dataclass(frozen=True)
class BrokerConfig:
    host: str
    port: int
    public_base: str
    allowed_origins: tuple[str, ...]
    seed_workspace: Path
    guest_config_template: Path | None
    sessions_root: Path
    state_path: Path
    usage_ledger: Path
    ip_hash_secret: bytes
    core_python: str
    port_start: int = 9100
    port_end: int = 9900
    port_step: int = 2
    max_concurrent: int = 150
    ttl_seconds: int = 900
    ready_timeout_seconds: float = 8.0
    ip_daily_sessions: int = 5
    session_token_limit: int = 30_000
    daily_token_limit: int = 1_500_000
    realtime_session_seconds: int = 180
    realtime_total_seconds: int = 600
    daily_realtime_seconds: int = 10_800


@dataclass
class Session:
    session_id: str
    token: str
    workspace: Path
    ready_file: Path
    usage_file: Path
    port: int
    process: subprocess.Popen[bytes]
    created_at: float
    expires_at: float
    last_activity: float
    ip_digest: str
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class SessionManager:
    def __init__(self, config: BrokerConfig, *, clock: Any = time.time) -> None:
        self.config = config
        self.clock = clock
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        # Ports and headroom claimed by a session that is still starting. Core
        # start-up is the slow part of `create`, and holding the manager lock
        # across it made every simultaneous visitor queue behind the first one.
        self._starting: dict[str, int] = {}
        self._stopping = threading.Event()
        self.config.sessions_root.mkdir(parents=True, exist_ok=True)
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        # A broker restart cannot re-authenticate or supervise old Core
        # processes. systemd kills the whole cgroup; remove only this service's
        # well-formed session directories and leave unexpected files untouched.
        for child in self.config.sessions_root.iterdir():
            if child.is_dir() and _safe_session_id(child.name):
                shutil.rmtree(child, ignore_errors=True)
        self._reaper = threading.Thread(target=self._reap_loop, name="joi-web-session-reaper", daemon=True)
        self._reaper.start()

    def create(self, client_ip: str) -> tuple[int, dict[str, Any]]:
        now = self.clock()
        with self._lock:
            self._reap_locked(now)
            if len(self._sessions) + len(self._starting) >= self.config.max_concurrent:
                return 429, {"ok": False, "error": "concurrency_limit", "message": "当前体验人数已满，请稍后再来。"}
            if self._daily_budget_exhausted():
                return 429, {"ok": False, "error": "daily_budget_exhausted", "message": "今天的体验名额已用完，请明天再来。"}
            ip_digest = self._ip_digest(client_ip)
            if self._ip_count(ip_digest) >= self.config.ip_daily_sessions:
                return 429, {"ok": False, "error": "ip_daily_limit", "message": "你今天的体验次数已用完。"}
            port = self._allocate_port_locked()
            if not port:
                return 429, {"ok": False, "error": "port_pool_exhausted", "message": "当前体验人数已满，请稍后再来。"}
            session_id = secrets.token_urlsafe(18).replace("-", "").replace("_", "")[:24]
            workspace = (self.config.sessions_root / session_id).resolve()
            if workspace.parent != self.config.sessions_root.resolve():
                return 500, {"ok": False, "error": "session_path_invalid"}
            # Claim the port and the concurrency slot, then start Core outside
            # the lock so simultaneous visitors start in parallel.
            self._starting[session_id] = port
            self._increment_ip(ip_digest)

        token = secrets.token_urlsafe(32)
        ready_file = workspace / ".joi-web-ready.json"
        usage_file = workspace / ".joi-web-usage.json"
        process: subprocess.Popen[bytes] | None = None
        try:
            self._materialize_workspace(workspace)
            process = self._spawn_core(session_id, token, workspace, ready_file, usage_file, port)
            self._wait_ready(process, ready_file, session_id)
        except Exception:
            self._terminate_process(process)
            shutil.rmtree(workspace, ignore_errors=True)
            with self._lock:
                self._starting.pop(session_id, None)
            return 503, {"ok": False, "error": "core_start_failed", "message": "Joi 暂时没有启动成功，请稍后重试。"}

        started = self.clock()
        session = Session(
            session_id=session_id,
            token=token,
            workspace=workspace,
            ready_file=ready_file,
            usage_file=usage_file,
            port=port,
            process=process,
            created_at=started,
            expires_at=started + self.config.ttl_seconds,
            last_activity=started,
            ip_digest=ip_digest,
        )
        with self._lock:
            self._starting.pop(session_id, None)
            if self._stopping.is_set():
                self._terminate_process(process)
                shutil.rmtree(workspace, ignore_errors=True)
                return 503, {"ok": False, "error": "broker_stopping", "message": "服务正在重启，请稍后再来。"}
            self._sessions[session_id] = session
            return 201, self.public_payload(session)

    def _materialize_workspace(self, workspace: Path) -> None:
        """Give the session its own workspace without copying the artwork again.

        A seed carrying real Live2D/VRM/MMD characters is tens of megabytes, and
        almost all of it is package artwork the visitor can only read: every RPC
        that installs, updates, removes or exports a character is outside the
        allowlist, and the mutable character state (`state.json`, `runtime/`)
        lives beside `packages/`, not inside it.

        So `packages/` is hardlinked and every other file is copied. The split
        matters: a hardlinked file that something later truncates in place is
        rewritten in the seed and in every other live session, and `config.yaml`
        is exactly such a file. Restricting the links to the one subtree nothing
        writes to keeps that whole class of accident impossible rather than
        merely unlikely, and costs nothing -- the rest of a seed is kilobytes.
        Hardlinks also make a session's own cleanup harmless: it drops a link,
        never the seed's bytes.
        """

        packages = self.config.seed_workspace / PACKAGES_RELATIVE
        shutil.copytree(
            self.config.seed_workspace,
            workspace,
            symlinks=False,
            ignore=shutil.ignore_patterns() if not packages.is_dir() else _ignore_packages,
        )
        if packages.is_dir():
            shutil.copytree(packages, workspace / PACKAGES_RELATIVE, symlinks=False, copy_function=_link_or_copy)
        if self.config.guest_config_template is not None:
            shutil.copy2(self.config.guest_config_template, workspace / "config.yaml")

    def public_payload(self, session: Session) -> dict[str, Any]:
        base = self.config.public_base.rstrip("/")
        ws_scheme = "wss" if base.startswith("https://") else "ws"
        ws_host = urllib.parse.urlsplit(base).netloc
        return {
            "ok": True,
            "session_id": session.session_id,
            "ws_url": f"{ws_scheme}://{ws_host}/ws/{session.session_id}",
            "asset_base": f"{base}/assets/{session.session_id}",
            "token": session.token,
            "expires_at": session.expires_at,
            "budget": {
                "token_limit": self.config.session_token_limit,
                "realtime_session_seconds": self.config.realtime_session_seconds,
                "realtime_total_seconds": self.config.realtime_total_seconds,
            },
        }

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.process.poll() is not None:
                if session is not None:
                    self._remove_locked(session)
                return None
            return session

    def authenticate(self, session_id: str, token: str) -> Session | None:
        session = self.get(session_id)
        if session is None or not hmac.compare_digest(session.token, str(token or "")):
            return None
        return session

    def touch(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.last_activity = self.clock()
            session.expires_at = session.last_activity + self.config.ttl_seconds

    def remove(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            self._remove_locked(session)
            return True

    def close(self) -> None:
        self._stopping.set()
        with self._lock:
            for session in list(self._sessions.values()):
                self._remove_locked(session)

    def usage(self, session: Session) -> dict[str, Any]:
        try:
            payload = json.loads(session.usage_file.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return {}

    def _spawn_core(
        self,
        session_id: str,
        token: str,
        workspace: Path,
        ready_file: Path,
        usage_file: Path,
        port: int,
    ) -> subprocess.Popen[bytes]:
        env = os.environ.copy()
        # CharacterPackageManager is workspace-bound. Leaving this inherited
        # variable set would point CollaborationStore somewhere else and split
        # one visitor's data across two roots.
        env.pop("JOI_DATA_HOME", None)
        public_asset_base = f"{self.config.public_base.rstrip('/')}/assets/{session_id}"
        command = [
            self.config.core_python,
            "-m", "agent_companion.core.server",
            "--workspace", str(workspace),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--session-token", token,
            "--instance-id", session_id,
            "--ready-file", str(ready_file),
            "--parent-pid", str(os.getpid()),
            "--public-asset-base", public_asset_base,
            "--allowed-origins", ",".join(self.config.allowed_origins),
            "--allowed-methods", ",".join(PUBLIC_RPC_METHODS),
            "--guest-session-token-limit", str(self.config.session_token_limit),
            "--guest-daily-token-limit", str(self.config.daily_token_limit),
            "--guest-realtime-session-seconds", str(self.config.realtime_session_seconds),
            "--guest-realtime-total-seconds", str(self.config.realtime_total_seconds),
            "--guest-daily-realtime-seconds", str(self.config.daily_realtime_seconds),
            "--guest-ledger", str(self.config.usage_ledger),
            "--guest-usage-file", str(usage_file),
        ]
        return subprocess.Popen(
            command,
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _wait_ready(self, process: subprocess.Popen[bytes], ready_file: Path, session_id: str) -> None:
        deadline = time.monotonic() + self.config.ready_timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("core_exited")
            try:
                payload = json.loads(ready_file.read_text(encoding="utf-8"))
                if payload.get("ready") is True and payload.get("instance_id") == session_id:
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.05)
        raise TimeoutError("core_ready_timeout")

    def _allocate_port_locked(self) -> int:
        used = {session.port for session in self._sessions.values()} | set(self._starting.values())
        for port in range(self.config.port_start, self.config.port_end + 1, self.config.port_step):
            if port in used:
                continue
            if _port_available(port) and _port_available(port + 1):
                return port
        return 0

    def _remove_locked(self, session: Session) -> None:
        self._sessions.pop(session.session_id, None)
        self._terminate_process(session.process)
        shutil.rmtree(session.workspace, ignore_errors=True)

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes] | None) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=4)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _reap_loop(self) -> None:
        while not self._stopping.wait(10):
            with self._lock:
                self._reap_locked(self.clock())

    def _reap_locked(self, now: float) -> None:
        for session in list(self._sessions.values()):
            if session.process.poll() is not None or now >= session.expires_at:
                self._remove_locked(session)

    def _ip_digest(self, client_ip: str) -> str:
        return hmac.new(self.config.ip_hash_secret, client_ip.encode("utf-8"), hashlib.sha256).hexdigest()

    def _ip_count(self, digest: str) -> int:
        payload = self._read_state()
        return max(0, int((payload.get("ips") or {}).get(digest) or 0))

    def _increment_ip(self, digest: str) -> None:
        payload = self._read_state()
        ips = payload.setdefault("ips", {})
        ips[digest] = max(0, int(ips.get(digest) or 0)) + 1
        self._write_state(payload)

    def _read_state(self) -> dict[str, Any]:
        today = _utc_day(self.clock())
        try:
            payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        if not isinstance(payload, dict) or payload.get("date") != today:
            return {"date": today, "ips": {}}
        return payload

    def _write_state(self, payload: dict[str, Any]) -> None:
        temporary = self.config.state_path.with_suffix(self.config.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(self.config.state_path)

    def _daily_budget_exhausted(self) -> bool:
        path = self.config.usage_ledger
        if not path.is_file():
            return False
        try:
            with path.open("r", encoding="utf-8") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                payload = json.load(handle)
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ValueError):
            # Existing sessions enforce atomically. An unreadable ledger is a
            # broker fault, so refuse new paid sessions rather than fail open.
            return True
        if not isinstance(payload, dict) or payload.get("date") != _utc_day(self.clock()):
            return False
        tokens_full = self.config.daily_token_limit and int(payload.get("tokens") or 0) >= self.config.daily_token_limit
        realtime_full = self.config.daily_realtime_seconds and int(payload.get("realtime_seconds") or 0) >= self.config.daily_realtime_seconds
        return bool(tokens_full or realtime_full)


class BrokerHandler(http.server.BaseHTTPRequestHandler):
    manager: SessionManager
    protocol_version = "HTTP/1.1"

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._origin_allowed():
            return self._json(403, {"ok": False, "error": "origin_forbidden"})
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/session":
            return self._json(404, {"ok": False, "error": "not_found"})
        if not self._origin_allowed():
            return self._json(403, {"ok": False, "error": "origin_forbidden"})
        if not self._consume_small_body():
            return
        status, payload = self.manager.create(self._client_ip())
        self._json(status, payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/ws/") and self.headers.get("Upgrade", "").casefold() == "websocket":
            return self._proxy_websocket(parsed)
        if parsed.path.startswith("/assets/"):
            return self._proxy_asset(parsed, head_only=False)
        session_id = _route_id(parsed.path, "/session/")
        if session_id:
            if not self._origin_allowed():
                return self._json(403, {"ok": False, "error": "origin_forbidden"})
            session = self.manager.authenticate(session_id, self._bearer_token())
            if session is None:
                return self._json(404, {"ok": False, "error": "session_not_found"})
            payload = self.manager.public_payload(session)
            payload["usage"] = self.manager.usage(session)
            return self._json(200, payload)
        self._json(404, {"ok": False, "error": "not_found"})

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/assets/"):
            return self._proxy_asset(parsed, head_only=True)
        self._json(404, {"ok": False, "error": "not_found"}, head_only=True)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        session_id = _route_id(parsed.path, "/session/")
        if not session_id:
            return self._json(404, {"ok": False, "error": "not_found"})
        if not self._origin_allowed():
            return self._json(403, {"ok": False, "error": "origin_forbidden"})
        session = self.manager.authenticate(session_id, self._bearer_token())
        if session is None:
            return self._json(404, {"ok": False, "error": "session_not_found"})
        self.manager.remove(session_id)
        self._json(200, {"ok": True})

    def _proxy_asset(self, parsed: urllib.parse.SplitResult, *, head_only: bool) -> None:
        parts = parsed.path.split("/", 3)
        if len(parts) < 4 or not _safe_session_id(parts[2]):
            return self._json(404, {"ok": False, "error": "not_found"}, head_only=head_only)
        session = self.manager.get(parts[2])
        if session is None:
            return self._json(404, {"ok": False, "error": "session_not_found"}, head_only=head_only)
        upstream_path = "/" + parts[3]
        if parsed.query:
            upstream_path += "?" + parsed.query
        connection = http.client.HTTPConnection("127.0.0.1", session.port + 1, timeout=10)
        headers = {
            key: value for key, value in self.headers.items()
            if key.casefold() not in HOP_BY_HOP and key.casefold() != "host"
        }
        try:
            connection.request("HEAD" if head_only else "GET", upstream_path, headers=headers)
            response = connection.getresponse()
            declared = int(response.getheader("Content-Length") or 0)
            if declared > MAX_ASSET_BYTES:
                return self._json(502, {"ok": False, "error": "asset_too_large"}, head_only=head_only)
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.casefold() not in HOP_BY_HOP and key.casefold() not in {"content-length", "server", "date"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(declared))
            self.end_headers()
            if not head_only:
                # Streamed, not buffered. A VRM body is tens of megabytes -- the
                # sample character alone is 26MB -- so reading it whole meant a
                # 16MB ceiling that silently 502'd every 3D character, and would
                # have held one full copy in memory per concurrent request.
                sent = 0
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    sent += len(chunk)
                    if sent > MAX_ASSET_BYTES:
                        # Headers are already out; truncating is the only signal
                        # left, and the browser reports the short read.
                        break
                    self.wfile.write(chunk)
            self.manager.touch(session.session_id)
        except (OSError, http.client.HTTPException):
            self._json(502, {"ok": False, "error": "asset_upstream_unavailable"}, head_only=head_only)
        finally:
            connection.close()

    def _proxy_websocket(self, parsed: urllib.parse.SplitResult) -> None:
        session_id = _route_id(parsed.path, "/ws/")
        if not session_id or not self._origin_allowed():
            return self._json(403, {"ok": False, "error": "origin_forbidden"})
        session = self.manager.get(session_id)
        if session is None:
            return self._json(404, {"ok": False, "error": "session_not_found"})
        upstream = socket.create_connection(("127.0.0.1", session.port), timeout=10)
        upstream.settimeout(None)
        target = "/" + ("?" + parsed.query if parsed.query else "")
        lines = [f"GET {target} HTTP/1.1\r\n"]
        for key, value in self.headers.items():
            if key.casefold() == "host":
                continue
            lines.append(f"{key}: {value}\r\n")
        lines.append(f"Host: 127.0.0.1:{session.port}\r\n\r\n")
        try:
            upstream.sendall("".join(lines).encode("latin-1"))
            self.connection.settimeout(None)
            sockets = [self.connection, upstream]
            while True:
                readable, _, errored = select.select(sockets, [], sockets, 30)
                if errored:
                    break
                if not readable:
                    continue
                for source in readable:
                    data = source.recv(64 * 1024)
                    if not data:
                        return
                    (upstream if source is self.connection else self.connection).sendall(data)
                    self.manager.touch(session_id)
        except OSError:
            return
        finally:
            upstream.close()
            self.close_connection = True

    def _origin_allowed(self) -> bool:
        origin = str(self.headers.get("Origin") or "").rstrip("/")
        return origin in self.manager.config.allowed_origins

    def _client_ip(self) -> str:
        remote = str(self.client_address[0] or "")
        try:
            remote_ip = ipaddress.ip_address(remote)
        except ValueError:
            return "invalid"
        if remote_ip.is_loopback:
            forwarded = str(self.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
            try:
                return str(ipaddress.ip_address(forwarded)) if forwarded else str(remote_ip)
            except ValueError:
                return str(remote_ip)
        return str(remote_ip)

    def _bearer_token(self) -> str:
        value = str(self.headers.get("Authorization") or "")
        scheme, _, token = value.partition(" ")
        return token.strip() if scheme.casefold() == "bearer" else ""

    def _consume_small_body(self) -> bool:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > 16 * 1024:
            self._json(413, {"ok": False, "error": "request_too_large"})
            return False
        if length:
            self.rfile.read(length)
        return True

    def _cors_headers(self) -> None:
        origin = str(self.headers.get("Origin") or "").rstrip("/")
        if origin in self.manager.config.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json(self, status: int, payload: dict[str, Any], *, head_only: bool = False) -> None:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        # Paths contain session ids and WS queries contain bearer tokens.
        return


class BrokerServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Keep a closed browser tab out of the log.

        Every visitor who navigates away resets a connection, and the stdlib
        prints a full traceback for each one. On a public page that is most of
        the log, which is how a real fault ends up unnoticed between hundreds of
        identical `ConnectionResetError` frames. Anything else still surfaces.
        """

        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


PACKAGES_RELATIVE = Path("data") / "agent_companion" / "characters" / "packages"


def _ignore_packages(directory: str, names: list[str]) -> set[str]:
    """Skip the packages subtree during the plain copy; it is linked after."""

    if Path(directory).name != "characters" or "packages" not in names:
        return set()
    return {"packages"}


def _link_or_copy(source: str, destination: str) -> None:
    """Hardlink a seed file, falling back to a copy across devices or on reuse."""

    try:
        os.link(source, destination)
    except (OSError, NotImplementedError, AttributeError):
        shutil.copy2(source, destination)


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", int(port)))
        except OSError:
            return False
    return True


def _safe_session_id(value: str) -> bool:
    return 16 <= len(value) <= 40 and value.isalnum()


def _route_id(path: str, prefix: str) -> str:
    value = path[len(prefix):] if path.startswith(prefix) else ""
    return value if _safe_session_id(value) and "/" not in value else ""


def _utc_day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()


def _http_base(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("public_base must be an http(s) origin")
    return raw


def _origins(value: str) -> tuple[str, ...]:
    rows: list[str] = []
    for item in value.split(","):
        raw = item.strip().rstrip("/")
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
            raise ValueError(f"invalid allowed origin: {raw}")
        rows.append(f"{parsed.scheme}://{parsed.netloc}")
    if not rows:
        raise ValueError("at least one allowed origin is required")
    return tuple(dict.fromkeys(rows))


def build_config(args: argparse.Namespace) -> BrokerConfig:
    seed = Path(args.seed_workspace).expanduser().resolve()
    if not seed.is_dir():
        raise ValueError("seed workspace does not exist")
    template = Path(args.guest_config_template).expanduser().resolve() if args.guest_config_template else None
    if template is not None and not template.is_file():
        raise ValueError("guest config template does not exist")
    if template is None and not (seed / "config.yaml").is_file():
        raise ValueError("seed workspace needs config.yaml or --guest-config-template")
    step = max(2, int(args.port_step))
    if not (1024 <= int(args.port_start) <= 65534) or not (1025 <= int(args.port_end) <= 65535):
        raise ValueError("port pool must use valid non-privileged ports")
    if int(args.port_end) <= int(args.port_start) + 1:
        raise ValueError("port pool must contain at least one port pair")
    if int(args.max_concurrent) < 1 or int(args.ttl_seconds) < 1 or float(args.ready_timeout_seconds) <= 0:
        raise ValueError("concurrency, TTL and ready timeout must be positive")
    if int(args.ip_daily_sessions) < 1:
        raise ValueError("IP daily session limit must be positive")
    if any(int(value) < 1 for value in (
        args.session_token_limit,
        args.daily_token_limit,
        args.realtime_session_seconds,
        args.realtime_total_seconds,
        args.daily_realtime_seconds,
    )):
        raise ValueError("guest cost limits must be positive")
    secret = os.environ.get("JOI_WEB_IP_HASH_SECRET", "").encode("utf-8")
    if len(secret) < 32:
        raise ValueError("JOI_WEB_IP_HASH_SECRET must contain at least 32 bytes")
    sessions_root = Path(args.sessions_root).expanduser().resolve()
    state_root = Path(args.state_root).expanduser().resolve()
    if sessions_root == Path("/") or state_root == Path("/"):
        raise ValueError("session and state roots must not be filesystem root")
    return BrokerConfig(
        host=args.host,
        port=args.port,
        public_base=_http_base(args.public_base),
        allowed_origins=_origins(args.allowed_origins),
        seed_workspace=seed,
        guest_config_template=template,
        sessions_root=sessions_root,
        state_path=state_root / "broker-state.json",
        usage_ledger=state_root / "usage-ledger.json",
        ip_hash_secret=secret,
        core_python=args.core_python,
        port_start=args.port_start,
        port_end=args.port_end,
        port_step=step,
        max_concurrent=args.max_concurrent,
        ttl_seconds=args.ttl_seconds,
        ready_timeout_seconds=args.ready_timeout_seconds,
        ip_daily_sessions=args.ip_daily_sessions,
        session_token_limit=args.session_token_limit,
        daily_token_limit=args.daily_token_limit,
        realtime_session_seconds=args.realtime_session_seconds,
        realtime_total_seconds=args.realtime_total_seconds,
        daily_realtime_seconds=args.daily_realtime_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the loopback Joi web session broker.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9080)
    parser.add_argument("--public-base", required=True, help="Public https origin terminating at Caddy")
    parser.add_argument("--allowed-origins", required=True, help="Comma-separated personal-site origins")
    parser.add_argument("--seed-workspace", required=True)
    parser.add_argument("--guest-config-template", default="")
    parser.add_argument("--sessions-root", default="/var/lib/joi-web/sessions")
    parser.add_argument("--state-root", default="/var/lib/joi-web/state")
    parser.add_argument("--core-python", default=sys.executable)
    parser.add_argument("--port-start", type=int, default=9100)
    parser.add_argument("--port-end", type=int, default=9900)
    parser.add_argument("--port-step", type=int, default=2)
    parser.add_argument("--max-concurrent", type=int, default=150)
    parser.add_argument("--ttl-seconds", type=int, default=900)
    parser.add_argument("--ready-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--ip-daily-sessions", type=int, default=5)
    parser.add_argument("--session-token-limit", type=int, default=30_000)
    parser.add_argument("--daily-token-limit", type=int, default=1_500_000)
    parser.add_argument("--realtime-session-seconds", type=int, default=180)
    parser.add_argument("--realtime-total-seconds", type=int, default=600)
    parser.add_argument("--daily-realtime-seconds", type=int, default=10_800)
    config = build_config(parser.parse_args(argv))
    if config.host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("broker must bind to loopback; expose only Caddy")
    manager = SessionManager(config)
    handler = type("ConfiguredBrokerHandler", (BrokerHandler,), {"manager": manager})
    server = BrokerServer((config.host, config.port), handler)

    def stop(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        manager.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
