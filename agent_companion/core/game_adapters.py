from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Protocol


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
        self._lock = threading.RLock()
        self._paused: set[str] = set()
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
                    "paused": adapter_id in self._paused,
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
                "paused": adapter_id in self._paused,
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
            self._state[adapter_id]["enabled"] = bool(enabled)
            self._save_state()
        return self.status(adapter_id)

    def detect(self, adapter_id: str) -> dict[str, Any]:
        if adapter_id == "ok-ww":
            runner = Path(os.environ.get("OK_WW_RUNNER", r"C:\Users\liujialuo\.codex\skills\github_issue_solver\scripts\run_ok_ww.ps1"))
            return {
                "available": sys.platform == "win32" and runner.is_file() and bool(shutil.which("powershell") or shutil.which("pwsh")),
                "platform": sys.platform,
                "runner_found": runner.is_file(),
                "status": "ready" if sys.platform == "win32" and runner.is_file() else "setup_required",
            }
        if adapter_id == "minecraft":
            command = _minecraft_bridge_command(self.workspace)
            return {
                "available": bool(command),
                "platform": sys.platform,
                "bridge_found": bool(command),
                "status": "ready" if command else "setup_required",
                "setup_hint": "配置 JOI_MINECRAFT_BRIDGE_COMMAND，或安装 Joi Mineflayer 桥接包。" if not command else "",
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
        if adapter_id == "minecraft" and mode == "takeover":
            return {
                "ok": True,
                "mode": mode,
                "handoff": "computer_use",
                "summary": "Minecraft 角色接管已准备；任何用户键鼠输入都会触发暂停。",
            }
        if adapter_id == "minecraft":
            return self._run_minecraft_bridge(mode, goal)
        return {"ok": False, "error": "adapter_run_unimplemented"}

    def pause(self, adapter_id: str) -> dict[str, Any]:
        if adapter_id not in self._manifests:
            return {"ok": False, "error": "adapter_not_found"}
        self._paused.add(adapter_id)
        bridge = self._bridge_control(adapter_id, "pause")
        return {"ok": True, "adapter_id": adapter_id, "paused": True, "bridge": bridge}

    def resume(self, adapter_id: str) -> dict[str, Any]:
        if adapter_id not in self._manifests:
            return {"ok": False, "error": "adapter_not_found"}
        self._paused.discard(adapter_id)
        bridge = self._bridge_control(adapter_id, "resume")
        return {"ok": True, "adapter_id": adapter_id, "paused": False, "bridge": bridge}

    def _run_minecraft_bridge(self, mode: str, goal: str) -> dict[str, Any]:
        command = _minecraft_bridge_command(self.workspace)
        if not command:
            return {"ok": False, "error": "minecraft_bridge_not_found"}
        payload = {"protocol": "joi.game_adapter.v1", "action": "run", "mode": mode, "goal": goal[:2_000], "pause_on_user_input": True}
        control_path = self.data_home / "minecraft-control.json"
        control_path.write_text(json.dumps({"action": "resume", "updated_at": time.time()}), encoding="utf-8")
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(payload, ensure_ascii=False) + "\n",
                text=True,
                capture_output=True,
                timeout=max(30, min(int(os.environ.get("JOI_MINECRAFT_TIMEOUT", "900")), 3600)),
                cwd=str(self.workspace),
                env={**os.environ, "JOI_GAME_ADAPTER_PROTOCOL": "joi.game_adapter.v1", "JOI_MINECRAFT_CONTROL_FILE": str(control_path)},
            )
        except (OSError, subprocess.TimeoutExpired):
            return {"ok": False, "error": "minecraft_bridge_unreachable"}
        response = _last_json_object(completed.stdout)
        return {
            "ok": completed.returncode == 0 and bool(response.get("ok", True)),
            "mode": mode,
            "bridge": response,
            "stderr": completed.stderr[-1_000:] if completed.returncode else "",
        }

    def _bridge_control(self, adapter_id: str, action: str) -> dict[str, Any]:
        if adapter_id != "minecraft" or not _minecraft_bridge_command(self.workspace):
            return {"sent": False}
        try:
            control_path = self.data_home / "minecraft-control.json"
            temporary = control_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"action": action, "updated_at": time.time()}), encoding="utf-8")
            temporary.replace(control_path)
        except OSError:
            return {"sent": False, "error": "control_file_unavailable"}
        return {"sent": True, "action": action}

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
            version="0.1.0",
            author="Joi adapter",
            license="MIT adapter; game and bridge licenses apply",
            platforms=("macos", "windows", "linux"),
            modes=("companion", "takeover"),
            detection=("structured_bridge", "foreground_window"),
            observation_sources=("mineflayer_state", "screen", "accessibility"),
            action_sets=("follow", "explore", "collect", "build", "keyboard", "mouse"),
            pause_strategy="user_input_or_bridge_pause",
            verification=("world_state", "inventory_delta", "screen_change"),
            checkpoint_strategy="world_position_and_save",
            source="builtin-reviewed-wrapper",
        ),
    )


def _minecraft_bridge_command(workspace: Path) -> list[str]:
    configured = str(os.environ.get("JOI_MINECRAFT_BRIDGE_COMMAND") or "").strip()
    if configured:
        executable = shutil.which(configured) or (str(Path(configured).expanduser()) if Path(configured).expanduser().is_file() else "")
        return [executable] if executable else []
    bundled = workspace / "agent_companion" / "adapters" / "minecraft-bridge" / "index.js"
    node = shutil.which("node")
    if node and bundled.is_file() and (bundled.parent / "node_modules" / "mineflayer").is_dir():
        return [node, str(bundled)]
    return []


def _last_json_object(output: str) -> dict[str, Any]:
    for line in reversed(str(output or "").splitlines()):
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}
