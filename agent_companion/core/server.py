from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.runtime_config_writer import preview_runtime_config_update
from agent_companion.core.schemas import AgentEvent, DisplayCard
from agent_companion.core.schemas import EventType
from agent_companion.core.runtime_status import build_runtime_status
from agent_companion.core.speech_input import AsrRuntimeState, SpeechInputProvider, build_asr_provider
from agent_companion.core.tts_bridge import TtsBridge
from agent_companion.core.voice import safe_voice_line


SPEAKABLE_EVENTS = {
    EventType.APPROVAL_REQUIRED,
    EventType.TOOL_STARTED,
    EventType.TOOL_COMPLETED,
    EventType.TOOL_FAILED,
    EventType.TASK_COMPLETED,
    EventType.TASK_FAILED,
}


class JsonRpcBridge:
    def __init__(
        self,
        workspace: Path,
        host: str = "127.0.0.1",
        port: int = 8765,
        asr_provider: SpeechInputProvider | None = None,
        asr_state: AsrRuntimeState | None = None,
        allow_mock_asr: bool = False,
    ) -> None:
        self.workspace = workspace.resolve()
        self.host = host
        self.port = port
        self.app = AgentCompanionApp(self.workspace)
        self.tts = TtsBridge(self.workspace)
        if asr_provider is None:
            self.asr, self.asr_state = build_asr_provider(self.workspace, allow_mock=allow_mock_asr)
        else:
            self.asr = asr_provider
            self.asr_state = asr_state or AsrRuntimeState(True, True, "injected")
        self.clients: set[Any] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue[AgentEvent] | None = None
        self._command_lock = threading.Lock()
        self._command_sequence = 0

    async def serve(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("缺少 websockets 依赖，请先安装 requirements.txt。") from exc

        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.app.bus.subscribe(self._on_event)
        async with websockets.serve(self._client_handler, self.host, self.port):
            print(f"Joi Core listening on ws://{self.host}:{self.port}")
            pump = asyncio.create_task(self._event_pump())
            try:
                await asyncio.Future()
            finally:
                pump.cancel()
                self.tts.shutdown()

    def _on_event(self, event: AgentEvent) -> None:
        if self.loop is None or self.queue is None:
            return
        self.loop.call_soon_threadsafe(self.queue.put_nowait, event)

    async def _client_handler(self, websocket: Any) -> None:
        self.clients.add(websocket)
        try:
            await websocket.send(json.dumps({"jsonrpc": "2.0", "method": "core.ready", "params": self._ready_payload()}, ensure_ascii=False))
            async for raw in websocket:
                await self._handle_message(websocket, raw)
        finally:
            self.clients.discard(websocket)

    async def _handle_message(self, websocket: Any, raw: str) -> None:
        request_id: Any = None
        try:
            payload = json.loads(raw)
            request_id = payload.get("id")
            method = str(payload.get("method") or "")
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            if method == "user.message":
                text = str(params.get("text") or "").strip()
                if not text:
                    await websocket.send(self._result(request_id, {"ok": False, "error": "empty_text"}))
                    return
                asyncio.create_task(asyncio.to_thread(self.submit_user_text, text))
                await websocket.send(self._result(request_id, {"ok": True, "submitted": True}))
                return
            if method == "approval.resolve":
                approval_id = str(params.get("approval_id") or "")
                approved = bool(params.get("approved", False))
                if not approval_id:
                    await websocket.send(self._result(request_id, {"ok": False, "error": "missing_approval_id"}))
                    return
                asyncio.create_task(asyncio.to_thread(self.resolve_approval_command, approval_id, approved))
                await websocket.send(self._result(request_id, {"ok": True, "submitted": True}))
                return
            if method == "runtime.config.preview":
                updates = params.get("updates")
                result = self.preview_runtime_config_update_command(updates if isinstance(updates, dict) else {})
                await websocket.send(self._result(request_id, result))
                return
            if method == "runtime.config.apply":
                updates = params.get("updates")
                result = self.apply_runtime_config_update_command(updates if isinstance(updates, dict) else {})
                await websocket.send(self._result(request_id, result))
                return
            if method == "semantic_target.select":
                selection_id = str(params.get("selection_id") or "").strip()
                rank = _safe_int(params.get("rank"))
                if not selection_id:
                    await websocket.send(self._result(request_id, {"ok": False, "error": "missing_selection_id"}))
                    return
                if rank is None or rank < 1:
                    await websocket.send(self._result(request_id, {"ok": False, "error": "invalid_rank"}))
                    return
                asyncio.create_task(asyncio.to_thread(self.select_semantic_target_command, selection_id, rank))
                await websocket.send(self._result(request_id, {"ok": True, "submitted": True}))
                return
            if method in {"voice.transcribe", "audio.transcribe"}:
                audio_base64 = str(params.get("audio_base64") or "")
                mime_type = str(params.get("mime_type") or "")
                result = await asyncio.to_thread(self.transcribe_and_submit, audio_base64, mime_type)
                await websocket.send(self._result(request_id, result))
                return
            if method == "artifact.read":
                artifact = str(params.get("artifact") or "")
                result = await asyncio.to_thread(self.read_artifact_command, artifact)
                await websocket.send(self._result(request_id, result))
                return
            if method == "core.ping":
                await websocket.send(self._result(request_id, {"ok": True}))
                return
            await websocket.send(self._error(request_id, -32601, f"unknown method: {method}"))
        except Exception as exc:
            await websocket.send(self._error(request_id, -32603, str(exc)[:500]))

    async def _event_pump(self) -> None:
        assert self.queue is not None
        while True:
            event = await self.queue.get()
            payload = event.to_dict()
            message = json.dumps({"jsonrpc": "2.0", "method": "agent.event", "params": payload}, ensure_ascii=False)
            await self._broadcast(message)
            if _event_applied_runtime_config(event):
                self._reload_runtime_after_config_change()
                await self._broadcast(json.dumps({"jsonrpc": "2.0", "method": "core.ready", "params": self._ready_payload()}, ensure_ascii=False))
            if event.type in SPEAKABLE_EVENTS:
                asyncio.create_task(self._synthesize_voice(event))

    async def _synthesize_voice(self, event: AgentEvent) -> None:
        audio = await asyncio.to_thread(self.tts.synthesize, event.voice_line.text, event.voice_line.sprite, event.voice_line.emotion)
        if not audio:
            return
        if not audio.get("voice_audio_path") and not audio.get("voice_audio_error"):
            return
        payload = {
            "task_id": event.task_id,
            "event_type": event.type.value,
            "event_created_at": event.created_at,
            "voice_text": event.voice_line.text,
            "voice_emotion": event.voice_line.emotion,
            "voice_sprite": event.voice_line.sprite,
            **audio,
        }
        data_url = self._voice_audio_data_url(audio.get("voice_audio_path", ""))
        if data_url:
            payload["voice_audio_data_url"] = data_url
        message = json.dumps({"jsonrpc": "2.0", "method": "agent.voice_audio", "params": payload}, ensure_ascii=False)
        await self._broadcast(message)

    def transcribe_and_submit(self, audio_base64: str, mime_type: str = "") -> dict[str, Any]:
        if _encoded_audio_exceeds_limit(audio_base64, self.asr_state.max_bytes):
            return self._asr_error("audio_too_large", _friendly_asr_message("audio_too_large"))
        audio = _decode_audio_base64(audio_base64)
        if audio_base64 and not audio:
            return self._asr_error("audio_decode_failed", _friendly_asr_message("audio_decode_failed"))
        if not self.asr_state.configured:
            return self._asr_error("asr_unconfigured", _friendly_asr_message(self.asr_state.error or "asr_unconfigured"))
        if len(audio) > self.asr_state.max_bytes:
            return self._asr_error("audio_too_large", _friendly_asr_message("audio_too_large"))
        result = self.asr.transcribe(audio, mime_type)
        if not result.ok:
            error_code = _safe_asr_error_code(result.error)
            return self._asr_error(error_code, _friendly_asr_message(error_code))
        payload: dict[str, Any] = {
            "ok": result.ok,
            "transcript": result.transcript,
            "confidence": result.confidence,
            "provider": result.provider,
            "submitted": False,
        }
        if result.ok:
            sequence, events = self._run_serial("voice.transcribe", lambda: self.app.handle_user_text(result.transcript))
            payload["submitted"] = True
            payload["sequence"] = sequence
            payload["events"] = [event.to_dict() for event in events]
        return payload

    def submit_user_text(self, text: str) -> dict[str, Any]:
        sequence, events = self._run_serial("user.message", lambda: self.app.handle_user_text(text))
        return {"ok": True, "submitted": True, "sequence": sequence, "events": [event.to_dict() for event in events]}

    def resolve_approval_command(self, approval_id: str, approved: bool) -> dict[str, Any]:
        sequence, events = self._run_serial("approval.resolve", lambda: self.app.resolve_approval(approval_id, approved))
        payload: dict[str, Any] = {"ok": True, "submitted": True, "sequence": sequence, "events": [event.to_dict() for event in events]}
        if any(_event_applied_runtime_config(event) for event in events):
            self._reload_runtime_after_config_change()
            payload["ready"] = self._ready_payload()
        return payload

    def select_semantic_target_command(self, selection_id: str, rank: int) -> dict[str, Any]:
        sequence, events = self._run_serial("semantic_target.select", lambda: self.app.select_semantic_target(selection_id, rank))
        return {"ok": True, "submitted": True, "sequence": sequence, "events": [event.to_dict() for event in events]}

    def preview_runtime_config_update_command(self, updates: dict[str, Any]) -> dict[str, Any]:
        result = preview_runtime_config_update(self.workspace, updates if isinstance(updates, dict) else {})
        return {"ok": result.ok, "preview": result.to_agent_state()}

    def apply_runtime_config_update_command(self, updates: dict[str, Any]) -> dict[str, Any]:
        preview = preview_runtime_config_update(self.workspace, updates if isinstance(updates, dict) else {})
        if not preview.ok:
            return {"ok": False, "submitted": False, "preview": preview.to_agent_state()}
        if not preview.changed:
            return {"ok": True, "submitted": False, "preview": preview.to_agent_state()}
        sequence, events = self._run_serial("runtime.config.apply", lambda: self.app.request_runtime_config_update(updates))
        return {"ok": True, "submitted": True, "preview": preview.to_agent_state(), "sequence": sequence, "events": [event.to_dict() for event in events]}

    def read_artifact_command(self, artifact: str) -> dict[str, Any]:
        path = self._resolve_artifact_path(artifact)
        if path is None or not path.is_file():
            return {"ok": False, "error": "artifact_not_found"}
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            return {"ok": False, "error": "unsupported_artifact_type"}
        if path.stat().st_size > 8 * 1024 * 1024:
            return {"ok": False, "error": "artifact_too_large"}
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {"ok": True, "artifact": artifact, "mime": mime, "data_url": f"data:{mime};base64,{encoded}"}

    def _resolve_artifact_path(self, artifact: str) -> Path | None:
        value = (artifact or "").strip()
        if not value or "\x00" in value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.workspace / value
        try:
            resolved = candidate.resolve()
            resolved.relative_to(self.workspace)
        except Exception:
            return None
        return resolved

    @staticmethod
    def _voice_audio_data_url(path_text: str) -> str:
        if not path_text:
            return ""
        path = Path(path_text)
        try:
            if not path.is_file() or path.suffix.lower() != ".wav" or path.stat().st_size > 5 * 1024 * 1024:
                return ""
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:audio/wav;base64,{encoded}"
        except Exception:
            return ""

    @staticmethod
    def _play_voice_audio_locally(path_text: str) -> None:
        if sys.platform != "win32" or not path_text:
            return
        path = Path(path_text)
        if not path.is_file() or path.suffix.lower() != ".wav":
            return
        try:
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            return

    def _run_serial(self, label: str, callback: Callable[[], list[AgentEvent]]) -> tuple[int, list[AgentEvent]]:
        with self._command_lock:
            self._command_sequence += 1
            sequence = self._command_sequence
            events = callback()
        return sequence, events

    def _asr_error(self, error: str, message: str) -> dict[str, Any]:
        error_code = _safe_asr_error_code(error)
        sequence, events = self._run_serial("voice.error", lambda: self._emit_voice_error(error_code, message))
        return {
            "ok": False,
            "submitted": False,
            "transcript": "",
            "error": error_code,
            "message": message,
            "sequence": sequence,
            "events": [event.to_dict() for event in events],
        }

    def _emit_voice_error(self, error: str, message: str) -> list[AgentEvent]:
        event = AgentEvent(
            EventType.TASK_FAILED,
            f"voice-{uuid.uuid4().hex[:8]}",
            DisplayCard("语音输入", message, status="failed"),
            safe_voice_line(message, fallback="语音输入没成功，请再试一次。", sprite="4"),
            {"event": "voice.error", "error": error},
        )
        self.app.bus.emit(event)
        return self.app.bus.drain()

    async def _broadcast(self, message: str) -> None:
        stale: list[Any] = []
        for client in list(self.clients):
            try:
                await client.send(message)
            except Exception:
                stale.append(client)
        for client in stale:
            self.clients.discard(client)

    def _ready_payload(self) -> dict[str, Any]:
        tts_status = self.tts.status_payload()
        payload: dict[str, Any] = {
            "workspace": str(self.workspace),
            "asr": {
                "enabled": self.asr_state.enabled,
                "configured": self.asr_state.configured,
                "provider": self.asr_state.provider,
                "max_seconds": self.asr_state.max_seconds,
                "max_bytes": self.asr_state.max_bytes,
                "timeout_seconds": self.asr_state.timeout_seconds,
                "error": self.asr_state.error,
            },
            "tts": tts_status,
            "runtime": build_runtime_status(self.workspace, self.asr_state, tts_status),
            "character": {
                "name": self.app.character.name,
                "sprites": [],
            },
        }
        config_path = self.workspace / "config.yaml"
        if not config_path.is_file():
            return payload
        try:
            from agent_companion.core.config import load_app_config

            config = load_app_config(config_path)
            payload["runtime_settings"] = _safe_runtime_settings(config)
            character = config.primary_character
            sprites: list[dict[str, str]] = []
            for sprite in character.sprites:
                image_path = (sprite.image_path or "").strip()
                if not image_path:
                    continue
                resolved = config.resolve_path(image_path).resolve()
                if not resolved.is_file():
                    continue
                sprites.append(
                    {
                        "id": sprite.id,
                        "label": sprite.label,
                        "image_path": str(resolved),
                        "image_data_url": self._image_data_url(resolved),
                    }
                )
            payload["character"] = {"name": character.name, "sprites": sprites}
        except Exception:
            return payload
        return payload

    def _reload_runtime_after_config_change(self) -> None:
        self.asr, self.asr_state = build_asr_provider(self.workspace)
        self.tts.reload()

    @staticmethod
    def _image_data_url(path: Path) -> str:
        try:
            data = path.read_bytes()
        except Exception:
            return ""
        mime = "image/webp" if path.suffix.lower() == ".webp" else mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> str:
        return json.dumps({"jsonrpc": "2.0", "id": request_id or f"server-{uuid.uuid4().hex[:8]}", "result": result}, ensure_ascii=False)

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> str:
        return json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}, ensure_ascii=False)


def _decode_audio_base64(audio_base64: str) -> bytes:
    if not audio_base64:
        return b""
    payload = audio_base64.split(",", 1)[1] if "," in audio_base64[:80] else audio_base64
    try:
        return base64.b64decode(payload, validate=False)
    except Exception:
        return b""


def _encoded_audio_exceeds_limit(audio_base64: str, max_bytes: int) -> bool:
    if not audio_base64:
        return False
    return _base64_payload_length(audio_base64) > _max_base64_length(max_bytes)


def _base64_payload_length(audio_base64: str) -> int:
    start = 0
    comma = audio_base64.find(",", 0, 120)
    if comma != -1:
        start = comma + 1
    return sum(1 for char in audio_base64[start:] if not char.isspace())


def _max_base64_length(max_bytes: int) -> int:
    return ((max(1, max_bytes) + 2) // 3) * 4


def _safe_asr_error_code(error: str) -> str:
    raw = (error or "asr_failed").strip().split(":", 1)[0].casefold()
    allowed = {
        "asr_unconfigured",
        "asr_disabled",
        "asr_config_error",
        "mock_asr_developer_only",
        "audio_too_large",
        "audio_decode_failed",
        "asr_timeout",
        "openai_package_missing",
        "empty_audio",
        "empty_transcript",
        "asr_failed",
    }
    if raw in allowed:
        return raw
    if "timeout" in raw:
        return "asr_timeout"
    return "asr_failed"


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _event_applied_runtime_config(event: AgentEvent) -> bool:
    state = event.agent_state or {}
    update = state.get("runtime_config_update")
    return event.type == EventType.TOOL_COMPLETED and isinstance(update, dict) and bool(update.get("ok")) and bool(update.get("changed")) and not bool(update.get("dry_run"))


def _safe_runtime_settings(config: Any) -> dict[str, Any]:
    return {
        "asr": {
            "enabled": bool(config.asr.enabled),
            "max_seconds": max(1, int(config.asr.max_seconds or 1)),
            "max_bytes": max(1024, int(config.asr.max_bytes or 1024)),
            "timeout_seconds": max(1, int(config.asr.timeout_seconds or 1)),
        },
        "tts": {
            "enabled": bool(config.tts.enabled),
            "volume": float(config.tts.volume),
            "speed_factor": float(config.tts.speed_factor),
            "fallback_to_system": bool(config.tts.fallback_to_system),
        },
        "ocr": {
            "timeout_seconds": max(1, int(config.ocr.timeout_seconds or 1)),
            "language": str(config.ocr.language or "chi_sim+eng"),
        },
        "llm": {
            "temperature": float(config.llm.temperature),
            "use_mock": bool(config.llm.use_mock),
        },
        "computer_use": {
            "post_action_settle_ms": max(0, int(config.computer_use.post_action_settle_ms or 0)),
        },
    }


def _friendly_asr_message(error: str) -> str:
    code = _safe_asr_error_code(error)
    messages = {
        "asr_unconfigured": "ASR 未配置，请先在设置里启用语音识别。",
        "asr_disabled": "语音识别还没有启用，请先在设置里打开。",
        "asr_config_error": "语音识别配置有问题，请检查设置后再试。",
        "mock_asr_developer_only": "当前语音识别只允许开发测试使用，请配置真实 ASR。",
        "audio_too_large": "这段语音太长了，我没有发送出去。",
        "audio_decode_failed": "这段语音没有读出来，请重新录一次。",
        "asr_timeout": "语音识别等太久了，我先停下，你可以再试一次。",
        "openai_package_missing": "语音识别组件还没准备好。",
        "empty_audio": "我没有录到声音，请再说一次。",
        "empty_transcript": "我没有听清楚，请再说一次。",
        "asr_failed": "语音识别没有成功，请再试一次。",
    }
    return messages.get(code, messages["asr_failed"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Joi WebSocket JSON-RPC bridge.")
    parser.add_argument("--workspace", default=".", help="Workspace root")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    bridge = JsonRpcBridge(Path(args.workspace), args.host, args.port)
    asyncio.run(bridge.serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
