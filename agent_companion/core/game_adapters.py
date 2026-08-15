from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from typing import Any, Callable, Protocol
import re

from agent_companion.core.ok_ww import ok_ww_runner_path, ok_ww_setup_hint
from agent_companion.core.minecraft_bridge import MinecraftBridgeClient


_MINECRAFT_HOST = re.compile(r"^(?:localhost|(?:[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?)|(?:\d{1,3}\.){3}\d{1,3})$")
_MINECRAFT_USERNAME = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_MINECRAFT_IDENTITY = re.compile(r"^[a-z0-9_:.-]{1,80}$")
_MINECRAFT_VERSION = re.compile(r"^\d+(?:\.\d+){1,3}$")


class GameAdapter(Protocol):
    manifest: "GameAdapterManifest"

    def detect(self) -> dict[str, Any]: ...
    def prepare(self, mode: str, goal: str, dry_run: bool) -> dict[str, Any]: ...
    def pause(self) -> dict[str, Any]: ...
    def resume(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GameAdapterManifest:
    id: str
    name: str
    version: str
    author: str
    license: str
    platforms: tuple[str, ...]
    modes: tuple[str, ...]
    detection: tuple[str, ...]
    observation_sources: tuple[str, ...]
    action_sets: tuple[str, ...]
    pause_strategy: str
    verification: tuple[str, ...]
    checkpoint_strategy: str
    source: str
    code_bearing: bool = True

    def payload(self) -> dict[str, Any]:
        return asdict(self)


class GameAdapterRegistry:
    """Lifecycle and protocol boundary for reviewed game code adapters."""

    def __init__(self, workspace: Path, data_home: Path) -> None:
        self.workspace = workspace.resolve()
        self.data_home = data_home.resolve()
        self.state_path = self.data_home / "game_adapters.json"
        self.minecraft_connection_path = self.data_home / "private" / "minecraft-connection.json"
        self.minecraft_checkpoint_dir = self.data_home / "private" / "minecraft-checkpoints"
        self._lock = threading.RLock()
        self._paused: set[str] = set()
        self._minecraft_sessions: dict[str, MinecraftBridgeClient] = {}
        self._minecraft_goals: dict[str, str] = {}
        self._minecraft_states: dict[str, str] = {}
        self._minecraft_checkpoints: dict[str, dict[str, Any]] = self._load_minecraft_checkpoints()
        self._manifests = {manifest.id: manifest for manifest in _builtin_manifests()}
        self._state = self._load_state()

    def list(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for adapter_id, manifest in self._manifests.items():
            detected = self.detect(adapter_id)
            rows.append(
                {
                    **manifest.payload(),
                    "installed": bool(self._state.get(adapter_id, {}).get("installed")),
                    "enabled": bool(self._state.get(adapter_id, {}).get("enabled")),
                    "paused": adapter_id in self._paused or (adapter_id == "minecraft" and "paused" in self._minecraft_states.values()),
                    "detection_status": detected,
                }
            )
        return {"ok": True, "adapters": rows}

    def status(self, adapter_id: str) -> dict[str, Any]:
        manifest = self._manifests.get(adapter_id)
        if not manifest:
            return {"ok": False, "error": "adapter_not_found"}
        state = self._state.get(adapter_id, {})
        return {
            "ok": True,
            "adapter": {
                **manifest.payload(),
                "installed": bool(state.get("installed")),
                "enabled": bool(state.get("enabled")),
                "paused": adapter_id in self._paused or (adapter_id == "minecraft" and "paused" in self._minecraft_states.values()),
                "detection_status": self.detect(adapter_id),
            },
        }

    def install(self, adapter_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        manifest = self._manifests.get(adapter_id)
        if not manifest:
            return {"ok": False, "error": "adapter_not_found"}
        if not confirmed:
            return {
                "ok": False,
                "error": "adapter_review_required",
                "requires_approval": True,
                "manifest": manifest.payload(),
            }
        with self._lock:
            self._state[adapter_id] = {"installed": True, "enabled": True, "installed_at": time.time()}
            self._save_state()
        return self.status(adapter_id)

    def uninstall(self, adapter_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if adapter_id not in self._manifests:
            return {"ok": False, "error": "adapter_not_found"}
        if not confirmed:
            return {"ok": False, "error": "confirmation_required"}
        with self._lock:
            if adapter_id == "minecraft":
                for session_id in list(self._minecraft_sessions):
                    self.stop_minecraft_session(session_id)
            self._state.pop(adapter_id, None)
            self._paused.discard(adapter_id)
            self._save_state()
        return {"ok": True, "uninstalled": adapter_id, "residuals": []}

    def set_enabled(self, adapter_id: str, enabled: bool) -> dict[str, Any]:
        if adapter_id not in self._manifests:
            return {"ok": False, "error": "adapter_not_found"}
        if not self._state.get(adapter_id, {}).get("installed"):
            return {"ok": False, "error": "adapter_not_installed"}
        with self._lock:
            if adapter_id == "minecraft" and not enabled:
                for session_id in list(self._minecraft_sessions):
                    self.stop_minecraft_session(session_id)
            self._state[adapter_id]["enabled"] = bool(enabled)
            self._save_state()
        return self.status(adapter_id)

    def detect(self, adapter_id: str) -> dict[str, Any]:
        if adapter_id == "ok-ww":
            runner = ok_ww_runner_path()
            ready = sys.platform == "win32" and runner is not None and bool(shutil.which("powershell") or shutil.which("pwsh"))
            return {
                "available": ready,
                "platform": sys.platform,
                "runner_found": runner is not None,
                "status": "ready" if ready else "setup_required",
                "setup_hint": "" if ready else ok_ww_setup_hint(),
            }
        if adapter_id == "minecraft":
            command = _minecraft_bridge_command(self.workspace)
            connection = self._minecraft_connection_environment()
            identity_configured = bool(connection.get("JOI_MINECRAFT_SERVER_ID") and connection.get("JOI_MINECRAFT_WORLD"))
            return {
                "available": bool(command) and identity_configured,
                "platform": sys.platform,
                "bridge_found": bool(command),
                "scope_identity_configured": identity_configured,
                "status": "ready" if command and identity_configured else "setup_required",
                "setup_hint": "安装/配置 Joi Mineflayer 桥接包，并设置 JOI_MINECRAFT_SERVER_ID 与 JOI_MINECRAFT_WORLD。" if not command or not identity_configured else "",
                "connection": self.minecraft_connection_status(),
            }
        return {"available": False, "status": "adapter_not_found"}

    def prepare(self, adapter_id: str, mode: str, goal: str, dry_run: bool = True) -> dict[str, Any]:
        manifest = self._manifests.get(adapter_id)
        state = self._state.get(adapter_id, {})
        if not manifest:
            return {"ok": False, "error": "adapter_not_found"}
        if not state.get("installed") or not state.get("enabled"):
            return {"ok": False, "error": "adapter_not_enabled"}
        if mode not in manifest.modes:
            return {"ok": False, "error": "unsupported_game_mode", "supported_modes": list(manifest.modes)}
        detection = self.detect(adapter_id)
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "adapter": manifest.payload(),
                "mode": mode,
                "goal": goal[:1_000],
                "detection_status": detection,
                "ready": bool(detection.get("available")),
            }
        if not detection.get("available"):
            return {"ok": False, "error": "adapter_setup_required", "detection_status": detection}
        if adapter_id == "ok-ww":
            return {"ok": True, "tool": "game.ok_ww.run", "arguments": {"intent": goal, "dry_run": False}, "mode": mode}
        if adapter_id == "minecraft":
            return {
                "ok": False,
                "error": "structured_session_required",
                "protocol": "joi.game_adapter.v2",
                "supported_modes": ["companion", "delegate"],
            }
        return {"ok": False, "error": "adapter_run_unimplemented"}

    def pause(self, adapter_id: str) -> dict[str, Any]:
        if adapter_id not in self._manifests:
            return {"ok": False, "error": "adapter_not_found"}
        if adapter_id == "minecraft":
            return {"ok": False, "error": "session_and_goal_required"}
        self._paused.add(adapter_id)
        return {"ok": True, "adapter_id": adapter_id, "paused": True}

    def resume(self, adapter_id: str) -> dict[str, Any]:
        if adapter_id not in self._manifests:
            return {"ok": False, "error": "adapter_not_found"}
        if adapter_id == "minecraft":
            return {"ok": False, "error": "session_and_goal_required"}
        self._paused.discard(adapter_id)
        return {"ok": True, "adapter_id": adapter_id, "paused": False}

    def start_minecraft_session(
        self,
        session_id: str,
        mode: str,
        scope: dict[str, Any],
        budget: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._state.get("minecraft", {})
        if not state.get("installed") or not state.get("enabled"):
            return {"ok": False, "error": "adapter_not_enabled"}
        if mode not in {"companion", "delegate"}:
            return {"ok": False, "error": "unsupported_game_mode"}
        connection = self._minecraft_connection_environment()
        configured_server = str(connection.get("JOI_MINECRAFT_SERVER_ID") or "").strip().casefold()
        configured_world = str(connection.get("JOI_MINECRAFT_WORLD") or "").strip().casefold()
        if not configured_server or not configured_world:
            return {"ok": False, "error": "minecraft_connection_scope_unconfigured"}
        if scope.get("server_id") != configured_server or scope.get("world") != configured_world:
            return {"ok": False, "error": "minecraft_connection_scope_mismatch"}
        command = _minecraft_bridge_command(self.workspace)
        if not command:
            return {"ok": False, "error": "minecraft_bridge_not_found"}
        with self._lock:
            if session_id in self._minecraft_sessions:
                return {"ok": False, "error": "minecraft_session_exists"}
        try:
            client = MinecraftBridgeClient(
                command,
                session_id=session_id,
                mode=mode,
                scope=scope,
                budget=budget,
                cwd=str(self.workspace),
                environment=connection,
                response_timeout=max(5, min(int(os.environ.get("JOI_MINECRAFT_TIMEOUT", "120")), 900)),
            )
        except (RuntimeError, ValueError, OSError):
            return {"ok": False, "error": "minecraft_bridge_unreachable"}
        result = client.start()
        if not result.get("ok"):
            # The bridge's own words about why it could not join, so the failure is not
            # just a code in the shell.
            detail = client.stderr_tail()
            if detail:
                print(f"minecraft bridge start failed ({result.get('error')}):\n{detail}", file=sys.stderr, flush=True)
            client.close()
            return result
        with self._lock:
            self._minecraft_sessions[session_id] = client
            self._minecraft_states[session_id] = "ready"
        return result

    def submit_minecraft_goal(
        self,
        session_id: str,
        goal_id: str,
        intent: dict[str, Any],
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_registered: Callable[[], None] | None = None,
        on_submitted: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            client = self._minecraft_sessions.get(session_id)
            if client is None:
                return {"ok": False, "error": "minecraft_session_not_found"}
            if session_id in self._minecraft_goals:
                return {"ok": False, "error": "minecraft_goal_already_running"}
            if self._minecraft_states.get(session_id) in {"recovery_required", "cancelled", "stopped"}:
                return {"ok": False, "error": "minecraft_session_not_runnable"}
            try:
                if cancel_requested is not None and cancel_requested():
                    return {"ok": False, "error": "goal_cancelled", "status": "cancelled", "verified": False, "changes": 0, "effects": 0}
            except Exception:
                return {"ok": False, "error": "goal_cancelled", "status": "cancelled", "verified": False, "changes": 0, "effects": 0}
            self._minecraft_goals[session_id] = goal_id
            self._minecraft_states[session_id] = "acting"
        if on_registered is not None:
            try:
                on_registered()
            except Exception:
                with self._lock:
                    self._minecraft_goals.pop(session_id, None)
                    self._minecraft_states[session_id] = "ready"
                return {"ok": False, "error": "goal_cancelled", "status": "cancelled", "verified": False, "changes": 0, "effects": 0, "zero_actions": True}
        if _cancellation_requested(cancel_requested):
            with self._lock:
                if self._minecraft_goals.get(session_id) == goal_id:
                    self._minecraft_goals.pop(session_id, None)
                self._minecraft_states[session_id] = "ready"
            return {"ok": False, "error": "goal_cancelled", "status": "cancelled", "verified": False, "changes": 0, "effects": 0, "zero_actions": True}
        try:
            if cancel_requested is None and on_submitted is None:
                result = client.submit_goal(goal_id, intent)
            else:
                result = client.submit_goal(
                    goal_id,
                    intent,
                    cancel_requested=cancel_requested,
                    on_submitted=on_submitted,
                )
        finally:
            with self._lock:
                if self._minecraft_goals.get(session_id) == goal_id:
                    self._minecraft_goals.pop(session_id, None)
        with self._lock:
            checkpoint = result.pop("checkpoint", {}) if isinstance(result.get("checkpoint"), dict) else {}
            if checkpoint:
                result["checkpoint_persisted"] = self._store_minecraft_checkpoint(session_id, goal_id, checkpoint, str(result.get("status") or "unknown"))
            if result.get("recovery_required"):
                self._minecraft_states[session_id] = "recovery_required"
            elif result.get("status") in {"cancelled", "partial"}:
                self._minecraft_states[session_id] = "ready"
            else:
                self._minecraft_states[session_id] = "ready"
        return result

    def pause_minecraft_goal(self, session_id: str, goal_id: str) -> dict[str, Any]:
        return self._minecraft_control(session_id, goal_id, "pause")

    def resume_minecraft_goal(self, session_id: str, goal_id: str) -> dict[str, Any]:
        return self._minecraft_control(session_id, goal_id, "resume")

    def cancel_minecraft_goal(self, session_id: str, goal_id: str) -> dict[str, Any]:
        return self._minecraft_control(session_id, goal_id, "cancel")

    def _minecraft_control(self, session_id: str, goal_id: str, action: str) -> dict[str, Any]:
        with self._lock:
            client = self._minecraft_sessions.get(session_id)
            active_goal = self._minecraft_goals.get(session_id)
        if client is None:
            return {"ok": False, "error": "minecraft_session_not_found"}
        if not goal_id or active_goal != goal_id:
            return {"ok": False, "error": "minecraft_goal_not_active"}
        result = getattr(client, action)(goal_id)
        with self._lock:
            if result.get("ok"):
                self._minecraft_states[session_id] = {"pause": "paused", "resume": "acting", "cancel": "ready"}[action]
                if action == "cancel":
                    self._minecraft_goals.pop(session_id, None)
            elif result.get("recovery_required"):
                self._minecraft_states[session_id] = "recovery_required"
        return result

    def stop_minecraft_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            client = self._minecraft_sessions.pop(session_id, None)
            self._minecraft_goals.pop(session_id, None)
        if client is None:
            return {"ok": False, "error": "minecraft_session_not_found"}
        result = client.stop()
        with self._lock:
            self._minecraft_states[session_id] = "stopped"
            self._minecraft_checkpoints.pop(session_id, None)
            self._delete_minecraft_checkpoint(session_id)
        return result

    def minecraft_session_status(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": session_id in self._minecraft_sessions,
                "state": self._minecraft_states.get(session_id, "not_found"),
                "active_goal": bool(self._minecraft_goals.get(session_id)),
            }

    def shutdown(self) -> None:
        with self._lock:
            clients = list(self._minecraft_sessions.values())
            self._minecraft_sessions.clear()
            self._minecraft_goals.clear()
        for client in clients:
            client.close()

    def configure_minecraft_connection(self, params: dict[str, Any]) -> dict[str, Any]:
        """Persist a typed, password-free connection profile outside the app."""

        required = {"host", "port", "username", "auth", "server_id", "world"}
        optional = {"version"}
        if set(params) - required - optional or not required.issubset(set(params)):
            return {"ok": False, "error": "minecraft_connection_invalid"}
        host = str(params.get("host") or "").strip().casefold()
        username = str(params.get("username") or "").strip()
        auth = str(params.get("auth") or "").strip().casefold()
        server_id = str(params.get("server_id") or "").strip().casefold()
        world = str(params.get("world") or "").strip().casefold()
        version = str(params.get("version") or "").strip()
        try:
            port = int(params.get("port"))
        except (TypeError, ValueError):
            port = 0
        if (
            not _MINECRAFT_HOST.fullmatch(host)
            or not 1 <= port <= 65535
            or not _MINECRAFT_USERNAME.fullmatch(username)
            or auth not in {"offline", "microsoft"}
            or not _MINECRAFT_IDENTITY.fullmatch(server_id)
            or not _MINECRAFT_IDENTITY.fullmatch(world)
            or (version and not _MINECRAFT_VERSION.fullmatch(version))
        ):
            return {"ok": False, "error": "minecraft_connection_invalid"}
        payload = {
            "host": host,
            "port": port,
            "username": username,
            "auth": auth,
            "server_id": server_id,
            "world": world,
            "version": version,
        }
        try:
            private_dir = self.minecraft_connection_path.parent
            private_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_dir.chmod(0o700)
            temporary = self.minecraft_connection_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(self.minecraft_connection_path)
            self.minecraft_connection_path.chmod(0o600)
        except OSError:
            return {"ok": False, "error": "minecraft_connection_store_failed"}
        return {"ok": True, "connection": self.minecraft_connection_status()}

    def minecraft_connection_status(self) -> dict[str, Any]:
        profile = self._load_minecraft_connection()
        if not profile:
            environment = self._environment_minecraft_connection()
            return {
                "configured": bool(environment),
                "source": "environment" if environment else "missing",
                "server_id": str(environment.get("JOI_MINECRAFT_SERVER_ID") or ""),
                "world": str(environment.get("JOI_MINECRAFT_WORLD") or ""),
            }
        return {
            "configured": True,
            "source": "private_core",
            "host": profile["host"],
            "port": profile["port"],
            "username": profile["username"],
            "auth": profile["auth"],
            "server_id": profile["server_id"],
            "world": profile["world"],
            "version": profile.get("version", ""),
        }

    def _load_minecraft_connection(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.minecraft_connection_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        required = {"host", "port", "username", "auth", "server_id", "world", "version"}
        return payload if set(payload) == required else {}

    def _environment_minecraft_connection(self) -> dict[str, str]:
        keys = (
            "JOI_MINECRAFT_HOST", "JOI_MINECRAFT_PORT", "JOI_MINECRAFT_USERNAME", "JOI_MINECRAFT_AUTH",
            "JOI_MINECRAFT_VERSION", "JOI_MINECRAFT_SERVER_ID", "JOI_MINECRAFT_WORLD",
        )
        values = {key: str(os.environ.get(key) or "").strip() for key in keys}
        required = {"JOI_MINECRAFT_HOST", "JOI_MINECRAFT_PORT", "JOI_MINECRAFT_USERNAME", "JOI_MINECRAFT_AUTH", "JOI_MINECRAFT_SERVER_ID", "JOI_MINECRAFT_WORLD"}
        return values if all(values.get(key) for key in required) else {}

    def _minecraft_connection_environment(self) -> dict[str, str]:
        profile = self._load_minecraft_connection()
        if not profile:
            return self._environment_minecraft_connection()
        private_profiles = self.data_home / "private" / "minecraft-profiles"
        try:
            private_profiles.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_profiles.chmod(0o700)
        except OSError:
            return {}
        result = {
            "JOI_MINECRAFT_HOST": str(profile["host"]),
            "JOI_MINECRAFT_PORT": str(profile["port"]),
            "JOI_MINECRAFT_USERNAME": str(profile["username"]),
            "JOI_MINECRAFT_AUTH": str(profile["auth"]),
            "JOI_MINECRAFT_SERVER_ID": str(profile["server_id"]),
            "JOI_MINECRAFT_WORLD": str(profile["world"]),
            "JOI_MINECRAFT_PROFILES_FOLDER": str(private_profiles),
        }
        if profile.get("version"):
            result["JOI_MINECRAFT_VERSION"] = str(profile["version"])
        vendor_dir = self.workspace / "agent_companion" / "adapters" / "minecraft-bridge" / "dist" / "vendor"
        if (vendor_dir / "minecraft-data" / "package.json").is_file():
            result["JOI_MINECRAFT_VENDOR_DIR"] = str(vendor_dir)
        return result

    def _load_minecraft_checkpoints(self) -> dict[str, dict[str, Any]]:
        loaded: dict[str, dict[str, Any]] = {}
        if not self.minecraft_checkpoint_dir.is_dir():
            return loaded
        for path in self.minecraft_checkpoint_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict) or set(payload) != {"session_id", "goal_id", "status", "checkpoint", "updated_at"}:
                continue
            session_id = str(payload.get("session_id") or "")
            goal_id = str(payload.get("goal_id") or "")
            checkpoint = payload.get("checkpoint")
            if not session_id.startswith("session-") or not goal_id or not isinstance(checkpoint, dict):
                continue
            loaded[session_id] = payload
        return loaded

    def _store_minecraft_checkpoint(self, session_id: str, goal_id: str, checkpoint: dict[str, Any], status: str) -> bool:
        payload = {
            "session_id": session_id,
            "goal_id": goal_id,
            "status": status[:40],
            "checkpoint": checkpoint,
            "updated_at": time.time(),
        }
        try:
            self.minecraft_checkpoint_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.minecraft_checkpoint_dir.chmod(0o700)
            path = self._minecraft_checkpoint_path(session_id)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(path)
            path.chmod(0o600)
        except OSError:
            return False
        self._minecraft_checkpoints[session_id] = payload
        return True

    def _delete_minecraft_checkpoint(self, session_id: str) -> None:
        try:
            self._minecraft_checkpoint_path(session_id).unlink(missing_ok=True)
        except OSError:
            return

    def _minecraft_checkpoint_path(self, session_id: str) -> Path:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return self.minecraft_checkpoint_dir / f"{digest}.json"

    def _load_state(self) -> dict[str, dict[str, Any]]:
        try:
            parsed = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)


def _cancellation_requested(check: Callable[[], bool] | None) -> bool:
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:
        return True


def _builtin_manifests() -> tuple[GameAdapterManifest, ...]:
    return (
        GameAdapterManifest(
            id="ok-ww",
            name="OK-WW",
            version="1.0.0",
            author="Joi adapter",
            license="External project license applies",
            platforms=("windows",),
            modes=("takeover",),
            detection=("runner_path", "foreground_window"),
            observation_sources=("screen", "ocr"),
            action_sets=("keyboard", "mouse"),
            pause_strategy="external_runner_stop",
            verification=("screen_change", "runner_exit_code"),
            checkpoint_strategy="game_save",
            source="builtin-reviewed-wrapper",
        ),
        GameAdapterManifest(
            id="minecraft",
            name="Minecraft",
            version="0.2.0",
            author="Joi adapter",
            license="MIT adapter; game and bridge licenses apply",
            platforms=("macos", "windows", "linux"),
            modes=("companion", "delegate"),
            detection=("structured_bridge", "foreground_window"),
            observation_sources=("mineflayer_state", "screen", "accessibility"),
            action_sets=("observe", "inventory", "follow_player", "come_to_player", "collect", "mine", "craft", "eat", "place_blueprint", "deposit"),
            pause_strategy="session_goal_ack_or_forced_termination",
            verification=("world_state", "inventory_delta", "screen_change"),
            checkpoint_strategy="private_core_checkpoint_no_auto_replay",
            source="builtin-reviewed-wrapper",
        ),
    )


def _minecraft_bridge_command(workspace: Path) -> list[str]:
    configured = str(os.environ.get("JOI_MINECRAFT_BRIDGE_COMMAND") or "").strip()
    if configured:
        executable_path = Path(configured).expanduser()
        executable = shutil.which(configured) or (str(executable_path.resolve()) if executable_path.is_file() else "")
        expected_digest = str(os.environ.get("JOI_MINECRAFT_BRIDGE_SHA256") or "").strip().casefold()
        if not executable or len(expected_digest) != 64:
            return []
        try:
            actual_digest = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
        except OSError:
            return []
        return [executable] if actual_digest == expected_digest else []
    built = workspace / "agent_companion" / "adapters" / "minecraft-bridge" / "dist" / "index.js"
    vendor = built.parent / "vendor" / "minecraft-data" / "package.json"
    packaged_node = built.parent / "runtime" / ("node.exe" if sys.platform == "win32" else "node")
    node = str(packaged_node) if packaged_node.is_file() and (sys.platform == "win32" or os.access(packaged_node, os.X_OK)) else shutil.which("node")
    if node and built.is_file() and vendor.is_file():
        return [node, str(built)]
    bundled = workspace / "agent_companion" / "adapters" / "minecraft-bridge" / "index.js"
    if node and bundled.is_file() and (bundled.parent / "node_modules" / "mineflayer").is_dir():
        return [node, str(bundled)]
    return []
