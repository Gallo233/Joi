from __future__ import annotations

from collections import OrderedDict, deque
import hashlib
import json
import os
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, Sequence
import uuid

from agent_companion.core.minecraft_contract import GAME_ACTIONS, MINECRAFT_PROTOCOL, MINECRAFT_PROTOCOL_VERSION


_MAX_MESSAGE_BYTES = 256 * 1024
_MAX_BUFFERED_EVENTS = 512
_MAX_SEEN_OUTPUT = 512
_SAFE_ENVIRONMENT_KEYS = frozenset(
    {
        "LANG",
        "LC_ALL",
        "PATH",
        "SYSTEMROOT",
        "TMPDIR",
        "WINDIR",
        "JOI_MINECRAFT_HOST",
        "JOI_MINECRAFT_PORT",
        "JOI_MINECRAFT_USERNAME",
        "JOI_MINECRAFT_AUTH",
        "JOI_MINECRAFT_VERSION",
        "JOI_MINECRAFT_PROFILES_FOLDER",
        "JOI_MINECRAFT_VENDOR_DIR",
        "JOI_MINECRAFT_SERVER_ID",
        "JOI_MINECRAFT_WORLD",
        "JOI_MINECRAFT_CONNECT_TIMEOUT_MS",
        "JOI_MINECRAFT_ACTION_TIMEOUT_MS",
        "JOI_MINECRAFT_FAKE",
        "JOI_MINECRAFT_FAKE_DELAY_MS",
        "JOI_MINECRAFT_FAKE_DISCONNECT_AFTER_EFFECT",
        "JOI_MINECRAFT_FAKE_IGNORE_CONTROL",
        "JOI_MINECRAFT_FAKE_IDLE_DISCONNECT_MS",
        "JOI_MINECRAFT_FAKE_PUSH_SNAPSHOT_MS",
        "JOI_MINECRAFT_FAKE_COMBAT_MS",
        "JOI_MINECRAFT_FAKE_CHAT_LINES",
        "JOI_MINECRAFT_VIEWER",
        "JOI_MINECRAFT_VIEWER_PORT",
    }
)
_OUTPUT_REQUIRED = {
    "protocol",
    "version",
    "type",
    "session_id",
    "message_id",
    "sequence",
    "bridge_instance_id",
    "reply_to",
    "payload",
}
_OUTPUT_OPTIONAL = {"goal_id"}
_OUTPUT_TYPES = frozenset(
    {
        "bridge.ready",
        "session.ready",
        "session.stopped",
        "goal.accepted",
        "goal.completed",
        "goal.failed",
        "goal.paused",
        "goal.resumed",
        "goal.cancelled",
        "state.snapshot",
        "combat.started",
        "combat.ended",
        "chat.observed",
        "recovery.required",
        "error",
    }
)
_BRIDGE_ERROR_CODES = frozenset(
    {
        "action_budget_exhausted",
        "after_state_required",
        "block_budget_exhausted",
        "block_not_found",
        "bridge_disconnected_after_uncertain_effect",
        "bridge_protocol_mismatch",
        "bridge_sequence_violation",
        "bridge_session_mismatch",
        "building_not_allowed",
        "cannot_dig_block",
        "collect_item_not_acquired",
        "container_not_found",
        "control_rejected",
        "deposit_item_not_found",
        "dimension_out_of_scope",
        "duplicate_in_progress",
        "goal_already_running",
        "goal_cancelled",
        "goal_not_active",
        "goal_not_paused",
        "goal_timeout",
        "hostile_not_found",
        "verification_failed",
        "invalid_blueprint",
        "invalid_bridge_envelope",
        "invalid_game_intent",
        "invalid_game_mode",
        "invalid_goal_payload",
        "invalid_session_payload",
        "message_id_conflict",
        "minecraft_connect_failed",
        "minecraft_connect_refused",
        "minecraft_disconnected",
        "minecraft_goal_failed",
        "minecraft_host_unreachable",
        "minecraft_login_rejected",
        "minecraft_version_unsupported",
        "missing_build_item",
        "missing_goal_id",
        "missing_reference_block",
        "player_not_found",
        "recipe_not_found",
        "recovery_required",
        "scope_identity_mismatch",
        "spatial_scope_exceeded",
        "spawn_timeout",
        "unknown_block",
        "unknown_game_action",
        "unknown_item",
        "unexpected_intent_field",
    }
)
_DIMENSIONS = frozenset({"overworld", "the_nether", "the_end"})


class MinecraftBridgeClient:
    """One fail-closed, session-scoped GameAdapter v2 child process.

    The client never restarts or replays an uncertain goal. A broken transport
    makes the session recovery-required and only an explicit new session can
    create a new bridge process.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        session_id: str,
        mode: str,
        scope: Mapping[str, Any],
        budget: Mapping[str, Any],
        cwd: str | None = None,
        environment: Mapping[str, str] | None = None,
        response_timeout: float = 30.0,
        control_timeout: float = 1.5,
    ) -> None:
        if not command or not str(command[0]).strip():
            raise ValueError("missing_minecraft_bridge_command")
        self.command = [str(item) for item in command]
        self.session_id = str(session_id)
        self.mode = str(mode)
        self.scope = dict(scope)
        self.budget = dict(budget)
        self.cwd = cwd
        self.response_timeout = max(1.0, min(float(response_timeout), 900.0))
        self.control_timeout = max(0.1, min(float(control_timeout), 10.0))
        self._condition = threading.Condition(threading.RLock())
        self._write_lock = threading.Lock()
        self._goal_dispatch_lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._seen_output: OrderedDict[str, str] = OrderedDict()
        self._event_listeners: list[Callable[[dict[str, Any]], None]] = []
        self._out_sequence = 0
        self._in_sequence = 0
        self._bridge_instance_id = ""
        self._transport_error = ""
        self._closed = False
        self._stderr_tail: deque[str] = deque(maxlen=20)
        child_env = _child_environment(environment)
        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=self.cwd,
                env=child_env,
            )
        except OSError as exc:
            raise RuntimeError("minecraft_bridge_unreachable") from exc
        self._reader = threading.Thread(target=self._read_stdout, name=f"minecraft-v2-{self.session_id}", daemon=True)
        self._stderr_reader = threading.Thread(target=self._drain_stderr, name=f"minecraft-v2-stderr-{self.session_id}", daemon=True)
        self._reader.start()
        self._stderr_reader.start()

    @property
    def alive(self) -> bool:
        return not self._closed and self._process.poll() is None and not self._transport_error

    def read_ready_for_test(self) -> dict[str, Any]:
        return self._wait_for(lambda event: event.get("type") == "bridge.ready", timeout=self.control_timeout)

    def add_event_listener(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Subscribe to unsolicited bridge events (``reply_to == ""``).

        The stdout reader thread invokes listeners with a plain-dict copy of
        the event; they must not block or call back into this client. Returns
        a disposer that removes the subscription.
        """

        with self._condition:
            self._event_listeners.append(callback)

        def remove() -> None:
            with self._condition:
                if callback in self._event_listeners:
                    self._event_listeners.remove(callback)

        return remove

    def request_snapshot(self, timeout: float | None = None) -> dict[str, Any]:
        """Ask the bridge for its sanitized live state (observation + checkpoint)."""

        try:
            request_id = self._send("state.snapshot.request", {})
            response = self._wait_for(
                lambda event: event.get("reply_to") == request_id and event.get("type") in {"state.snapshot", "error"},
                timeout=timeout or self.response_timeout,
            )
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc), "recovery_required": True}
        if response.get("type") != "state.snapshot":
            return {"ok": False, "error": _event_error(response, "state_snapshot_rejected")}
        payload = response.get("payload") if isinstance(response.get("payload"), dict) else {}
        return {
            "ok": True,
            "observation": _safe_observation(payload.get("observation")),
            "checkpoint": _safe_checkpoint(payload.get("checkpoint")),
            "bridge_instance_id": self._bridge_instance_id,
        }

    def start(self) -> dict[str, Any]:
        try:
            ready = self.read_ready_for_test()
            if ready.get("type") != "bridge.ready":
                return {"ok": False, "error": "bridge_not_ready"}
            request_id = self._send("session.start", {"mode": self.mode, "scope": self.scope, "budget": self.budget})
            response = self._wait_for(
                lambda event: event.get("reply_to") == request_id and event.get("type") in {"session.ready", "error"},
                timeout=self.response_timeout,
            )
        except RuntimeError as exc:
            self._terminate()
            return {"ok": False, "error": str(exc), "recovery_required": True}
        if response.get("type") != "session.ready":
            return {"ok": False, "error": _event_error(response, "bridge_session_rejected")}
        return {
            "ok": True,
            "state": "ready",
            "bridge_instance_id": self._bridge_instance_id,
            "capabilities": _safe_capabilities(response.get("payload")),
        }

    def submit_goal(
        self,
        goal_id: str,
        intent: Mapping[str, Any],
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_submitted: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        try:
            with self._goal_dispatch_lock:
                if _cancellation_requested(cancel_requested):
                    return {
                        "ok": False,
                        "error": "goal_cancelled",
                        "status": "cancelled",
                        "verified": False,
                        "changes": 0,
                        "effects": 0,
                        "zero_actions": True,
                    }
                request_id = self._send("goal.submit", {"intent": dict(intent)}, goal_id=goal_id)
                if on_submitted is not None:
                    try:
                        on_submitted()
                    except Exception as exc:
                        self._terminate()
                        raise RuntimeError("minecraft_bridge_dispatch_failed") from exc
            response = self._wait_for(
                lambda event: event.get("goal_id") == goal_id
                and event.get("reply_to") == request_id
                and event.get("type") in {"goal.completed", "goal.failed", "goal.cancelled", "recovery.required", "error"},
                timeout=self.response_timeout,
            )
        except RuntimeError as exc:
            self._terminate()
            return {
                "ok": False,
                "error": str(exc),
                "status": "unverified",
                "verified": False,
                "recovery_required": True,
                "bridge_instance_id": self._bridge_instance_id,
            }
        payload = response.get("payload") if isinstance(response.get("payload"), dict) else {}
        if response.get("type") != "goal.completed":
            child_status = str(payload.get("status") or "failed")
            if child_status not in {"partial", "unverified", "failed", "cancelled"}:
                child_status = "failed"
            return {
                "ok": False,
                "error": _event_error(response, "minecraft_goal_failed"),
                "status": child_status,
                "verified": False,
                "changes": _safe_int(payload.get("changes")),
                "effects": _safe_int(payload.get("effects")),
                "before": _safe_observation(payload.get("before")),
                "after": _safe_observation(payload.get("after")),
                "recovery_required": response.get("type") == "recovery.required" or bool(payload.get("recovery_required")),
                "checkpoint": _safe_checkpoint(payload.get("checkpoint")),
                "bridge_instance_id": self._bridge_instance_id,
            }
        before = _safe_observation(payload.get("before"))
        after = _safe_observation(payload.get("after"))
        verified = payload.get("verified") is True and bool(after)
        return {
            "ok": bool(verified),
            "error": "" if verified else "after_state_required",
            "status": "completed" if verified else "unverified",
            "verified": verified,
            "summary": "verified_action",
            "before": before,
            "after": after,
            "changes": _safe_int(payload.get("changes")),
            "effects": _safe_int(payload.get("effects")),
            "checkpoint": _safe_checkpoint(payload.get("checkpoint")),
            "bridge_instance_id": self._bridge_instance_id,
        }

    def pause(self, goal_id: str) -> dict[str, Any]:
        return self._control("goal.pause", "goal.paused", goal_id)

    def resume(self, goal_id: str) -> dict[str, Any]:
        return self._control("goal.resume", "goal.resumed", goal_id)

    def cancel(self, goal_id: str) -> dict[str, Any]:
        return self._control("goal.cancel", "goal.cancelled", goal_id)

    def stop(self) -> dict[str, Any]:
        if self._closed:
            return {"ok": True, "acknowledged": True, "already_closed": True}
        result = self._control("session.stop", "session.stopped", "", session_control=True)
        self._terminate()
        return result

    def close(self) -> None:
        if self._closed:
            return
        if self._process.poll() is None:
            try:
                self.stop()
            except Exception:
                self._terminate()
        else:
            self._terminate()

    def _control(self, message_type: str, response_type: str, goal_id: str, *, session_control: bool = False) -> dict[str, Any]:
        try:
            with self._goal_dispatch_lock:
                request_id = self._send(message_type, {}, goal_id="" if session_control else goal_id)
            response = self._wait_for(
                lambda event: event.get("reply_to") == request_id and event.get("type") in {response_type, "error"},
                timeout=self.control_timeout,
            )
        except RuntimeError as exc:
            self._terminate()
            return {
                "ok": False,
                "error": str(exc),
                "acknowledged": False,
                "forced_terminated": True,
                "recovery_required": message_type not in {"goal.cancel", "session.stop"},
            }
        if response.get("type") != response_type:
            self._terminate()
            return {
                "ok": False,
                "error": _event_error(response, "control_rejected"),
                "acknowledged": False,
                "forced_terminated": True,
                "recovery_required": message_type not in {"goal.cancel", "session.stop"},
            }
        return {"ok": True, "acknowledged": True, "state": response_type.split(".")[-1]}

    def _send(self, message_type: str, payload: Mapping[str, Any], *, goal_id: str = "") -> str:
        if self._closed or self._process.poll() is not None or self._transport_error:
            raise RuntimeError(self._transport_error or "minecraft_bridge_disconnected")
        with self._write_lock:
            self._out_sequence += 1
            message_id = f"core-{uuid.uuid4().hex}"
            envelope: dict[str, Any] = {
                "protocol": MINECRAFT_PROTOCOL,
                "version": MINECRAFT_PROTOCOL_VERSION,
                "type": message_type,
                "session_id": self.session_id,
                "message_id": message_id,
                "sequence": self._out_sequence,
                "payload": dict(payload),
            }
            if goal_id:
                envelope["goal_id"] = str(goal_id)
            encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > _MAX_MESSAGE_BYTES:
                raise RuntimeError("bridge_message_too_large")
            try:
                assert self._process.stdin is not None
                self._process.stdin.write(encoded + "\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._set_transport_error("minecraft_bridge_disconnected")
                raise RuntimeError("minecraft_bridge_disconnected") from exc
            return message_id

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        try:
            for line in self._process.stdout:
                if len(line.encode("utf-8", errors="replace")) > _MAX_MESSAGE_BYTES:
                    self._set_transport_error("bridge_message_too_large")
                    break
                try:
                    event = json.loads(line)
                    self._validate_event(event)
                except (ValueError, TypeError, MinecraftBridgeProtocolError) as exc:
                    self._set_transport_error(str(exc) or "invalid_bridge_message")
                    break
                digest = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                message_id = str(event["message_id"])
                unsolicited = str(event.get("reply_to") or "") == ""
                with self._condition:
                    previous = self._seen_output.get(message_id)
                    if previous and previous != digest:
                        self._transport_error = "bridge_message_id_conflict"
                        self._condition.notify_all()
                        break
                    if previous:
                        continue
                    self._seen_output[message_id] = digest
                    self._seen_output.move_to_end(message_id)
                    while len(self._seen_output) > _MAX_SEEN_OUTPUT:
                        self._seen_output.popitem(last=False)
                    self._events.append(event)
                    if len(self._events) > _MAX_BUFFERED_EVENTS:
                        self._events = [row for row in self._events if not row.get("_consumed")][-_MAX_BUFFERED_EVENTS:]
                    self._condition.notify_all()
                    listeners = list(self._event_listeners) if unsolicited else []
                for listener in listeners:
                    try:
                        listener({key: value for key, value in event.items() if key != "_consumed"})
                    except Exception:
                        pass
        except ValueError:
            # _terminate() closed the pipe under this reader; shutdown owns the error code.
            pass
        finally:
            if not self._closed:
                self._set_transport_error("minecraft_bridge_disconnected")

    def _validate_event(self, event: Any) -> None:
        if not isinstance(event, dict):
            raise MinecraftBridgeProtocolError("invalid_bridge_message")
        fields = set(event)
        if not _OUTPUT_REQUIRED.issubset(fields) or fields - _OUTPUT_REQUIRED - _OUTPUT_OPTIONAL:
            raise MinecraftBridgeProtocolError("invalid_bridge_envelope")
        if event.get("protocol") != MINECRAFT_PROTOCOL or event.get("version") != MINECRAFT_PROTOCOL_VERSION:
            raise MinecraftBridgeProtocolError("bridge_protocol_mismatch")
        if event.get("type") not in _OUTPUT_TYPES:
            raise MinecraftBridgeProtocolError("unknown_bridge_message_type")
        if (
            not isinstance(event.get("payload"), dict)
            or not isinstance(event.get("sequence"), int)
            or not isinstance(event.get("message_id"), str)
            or not event.get("message_id")
            or not isinstance(event.get("reply_to"), str)
        ):
            raise MinecraftBridgeProtocolError("invalid_bridge_envelope")
        if (str(event.get("type") or "").startswith("goal.") or event.get("type") == "recovery.required") and not str(event.get("goal_id") or ""):
            raise MinecraftBridgeProtocolError("missing_goal_id")
        _validate_event_payload(str(event.get("type") or ""), event["payload"])
        expected = self._in_sequence + 1
        if event["sequence"] != expected:
            raise MinecraftBridgeProtocolError("bridge_sequence_violation")
        self._in_sequence = event["sequence"]
        bridge_instance_id = str(event.get("bridge_instance_id") or "")
        if not bridge_instance_id:
            raise MinecraftBridgeProtocolError("missing_bridge_instance_id")
        if self._bridge_instance_id and bridge_instance_id != self._bridge_instance_id:
            raise MinecraftBridgeProtocolError("bridge_instance_changed")
        self._bridge_instance_id = bridge_instance_id
        session_id = str(event.get("session_id") or "")
        if event.get("type") == "bridge.ready":
            if session_id:
                raise MinecraftBridgeProtocolError("bridge_ready_session_mismatch")
        elif session_id != self.session_id:
            raise MinecraftBridgeProtocolError("bridge_session_mismatch")

    def _wait_for(self, predicate: Callable[[dict[str, Any]], bool], *, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for event in self._events:
                    if not event.get("_consumed") and predicate(event):
                        event["_consumed"] = True
                        return {key: value for key, value in event.items() if key != "_consumed"}
                if self._transport_error:
                    raise RuntimeError(self._transport_error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("minecraft_bridge_ack_timeout")
                self._condition.wait(timeout=remaining)

    def _set_transport_error(self, code: str) -> None:
        with self._condition:
            if not self._transport_error:
                self._transport_error = code
            self._condition.notify_all()

    def stderr_tail(self) -> str:
        """The child's last diagnostic lines, so a failed connect can be explained."""
        with self._condition:
            return "\n".join(self._stderr_tail)

    def _drain_stderr(self) -> None:
        assert self._process.stderr is not None
        try:
            for line in self._process.stderr:
                trimmed = line.strip()[:400]
                if not trimmed:
                    continue
                with self._condition:
                    self._stderr_tail.append(trimmed)
        except (OSError, ValueError):
            return

    def _terminate(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        with self._condition:
            if not self._transport_error:
                self._transport_error = "minecraft_bridge_terminated"
            self._condition.notify_all()


class MinecraftBridgeProtocolError(RuntimeError):
    pass


def _child_environment(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    source: dict[str, str] = {key: value for key, value in os.environ.items() if key in _SAFE_ENVIRONMENT_KEYS}
    for key, value in (overrides or {}).items():
        if key in _SAFE_ENVIRONMENT_KEYS:
            source[key] = str(value)
    source["JOI_GAME_ADAPTER_PROTOCOL"] = f"{MINECRAFT_PROTOCOL}.v{MINECRAFT_PROTOCOL_VERSION}"
    return source


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _safe_capabilities(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("capabilities"), list):
        return []
    return [str(value) for value in payload["capabilities"] if str(value) in GAME_ACTIONS]


def _event_error(event: Mapping[str, Any], fallback: str) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    code = str(payload.get("error") or fallback)
    return code if code in _BRIDGE_ERROR_CODES else fallback


def _safe_observation(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    required = {"dimension", "health", "food", "inventory_slots", "inventory_total"}
    if set(value) - required - {"nearby_hostiles", "world"} or not required.issubset(set(value)):
        return {}
    dimension = str(value.get("dimension") or "").replace("minecraft:", "")
    if dimension not in _DIMENSIONS:
        return {}
    observation = {
        "dimension": dimension,
        "health": _bounded_number(value.get("health"), 0, 40),
        "food": _bounded_number(value.get("food"), 0, 40),
        "inventory_slots": min(_safe_int(value.get("inventory_slots")), 128),
        "inventory_total": min(_safe_int(value.get("inventory_total")), 100_000),
    }
    hostiles = value.get("nearby_hostiles")
    if isinstance(hostiles, list):
        rows: list[dict[str, Any]] = []
        for row in hostiles[:16]:
            if not isinstance(row, Mapping):
                continue
            name = str(row.get("name") or "").strip().casefold()
            count = _safe_int(row.get("count"))
            if not name.replace("_", "").replace(":", "").isalnum() or len(name) > 40 or not 1 <= count <= 256:
                continue
            rows.append({"name": name, "count": count})
        observation["nearby_hostiles"] = rows
    world = value.get("world")
    if isinstance(world, Mapping):
        safe_world: dict[str, Any] = {}
        time_of_day = _safe_int(world.get("time_of_day"))
        if 0 <= time_of_day <= 24_000:
            safe_world["time_of_day"] = time_of_day
        if isinstance(world.get("raining"), bool):
            safe_world["raining"] = world["raining"]
        entities = world.get("entities")
        if isinstance(entities, list):
            rows = []
            for row in entities[:16]:
                if not isinstance(row, Mapping):
                    continue
                entity_type = str(row.get("type") or "").strip().casefold()
                name = str(row.get("name") or "").strip().casefold()
                kind = str(row.get("kind") or "").strip().casefold()
                count = _safe_int(row.get("count"))
                if entity_type not in {"player", "mob", "object", "animal", "unknown"} or len(name) > 40 or len(kind) > 40:
                    continue
                if not name.replace("_", "").replace(":", "").isalnum() or not kind.replace("_", "").replace(":", "").isalnum():
                    continue
                if not 1 <= count <= 256:
                    continue
                entry: dict[str, Any] = {"type": entity_type, "name": name, "count": count}
                if kind:
                    entry["kind"] = kind
                rows.append(entry)
            safe_world["entities"] = rows
        observation["world"] = safe_world
    return observation


def _safe_checkpoint(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    if not {"position", "dimension", "health", "food", "inventory"}.issubset(set(value)):
        return {}
    if set(value) - {"position", "dimension", "health", "food", "inventory", "blocks_changed"}:
        return {}
    observation = _safe_observation(
        {
            "dimension": value.get("dimension"),
            "health": value.get("health"),
            "food": value.get("food"),
            "inventory_slots": len(value.get("inventory") or []) if isinstance(value.get("inventory"), list) else 0,
            "inventory_total": sum(_safe_int(row.get("count")) for row in value.get("inventory", []) if isinstance(row, Mapping)) if isinstance(value.get("inventory"), list) else 0,
        }
    )
    position = value.get("position")
    if not observation or not isinstance(position, Mapping):
        return {}
    coordinates: dict[str, float] = {}
    for axis in ("x", "y", "z"):
        number = _bounded_number(position.get(axis), -30_000_000, 30_000_000)
        coordinates[axis] = number
    inventory: list[dict[str, Any]] = []
    if isinstance(value.get("inventory"), list):
        for row in value["inventory"][:128]:
            if not isinstance(row, Mapping):
                continue
            name = str(row.get("name") or "")
            if not name.replace("_", "").replace(":", "").isalnum() or len(name) > 80:
                continue
            inventory.append({"name": name, "count": min(_safe_int(row.get("count")), 64)})
    return {**observation, "position": coordinates, "inventory": inventory}


def _bounded_number(value: Any, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        return minimum
    try:
        number = float(value)
    except (TypeError, ValueError):
        return minimum
    if number != number or number in {float("inf"), float("-inf")}:
        return minimum
    return max(minimum, min(number, maximum))


def _cancellation_requested(check: Callable[[], bool] | None) -> bool:
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:
        return True


def _validate_event_payload(message_type: str, payload: Mapping[str, Any]) -> None:
    schemas: dict[str, tuple[set[str], set[str]]] = {
        "bridge.ready": ({"capabilities", "fake"}, set()),
        "session.ready": ({"capabilities", "state"}, set()),
        "session.stopped": ({"state"}, set()),
        "goal.accepted": ({"state"}, set()),
        "goal.completed": ({"verified", "status", "summary", "changes", "effects", "before", "after", "checkpoint"}, {"replayed"}),
        "goal.failed": ({"verified", "status", "changes", "effects", "before", "after", "checkpoint"}, {"error", "summary", "replayed"}),
        "goal.paused": ({"state"}, set()),
        "goal.resumed": ({"state"}, set()),
        "goal.cancelled": ({"state", "status", "verified", "changes", "effects", "before", "after", "checkpoint"}, {"replayed"}),
        "state.snapshot": ({"observation", "checkpoint"}, set()),
        "combat.started": ({"state"}, set()),
        "combat.ended": ({"state"}, set()),
        "chat.observed": ({"player", "text"}, set()),
        "recovery.required": ({"error", "verified", "status", "changes", "effects", "recovery_required", "before", "after", "checkpoint"}, {"replayed"}),
        "error": ({"error"}, set()),
    }
    required, optional = schemas[message_type]
    optional = optional | ({"replayed"} if message_type != "bridge.ready" else set())
    fields = set(payload)
    if not required.issubset(fields) or fields - required - optional:
        raise MinecraftBridgeProtocolError("invalid_bridge_payload")
