from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.schemas import AgentEvent
from agent_companion.core.schemas import EventType
from agent_companion.core.speech_input import AsrRuntimeState, SpeechInputProvider, build_asr_provider
from agent_companion.core.tts_bridge import TtsBridge


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
            if method in {"voice.transcribe", "audio.transcribe"}:
                audio_base64 = str(params.get("audio_base64") or "")
                mime_type = str(params.get("mime_type") or "")
                result = await asyncio.to_thread(self.transcribe_and_submit, audio_base64, mime_type)
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
            if event.type in SPEAKABLE_EVENTS:
                asyncio.create_task(self._synthesize_voice(event))

    async def _synthesize_voice(self, event: AgentEvent) -> None:
        audio = await asyncio.to_thread(self.tts.synthesize, event.voice_line.text, event.voice_line.sprite)
        if not audio or not audio.get("voice_audio_path"):
            return
        payload = {
            "task_id": event.task_id,
            "event_type": event.type.value,
            "voice_text": event.voice_line.text,
            **audio,
        }
        message = json.dumps({"jsonrpc": "2.0", "method": "agent.voice_audio", "params": payload}, ensure_ascii=False)
        await self._broadcast(message)

    def transcribe_and_submit(self, audio_base64: str, mime_type: str = "") -> dict[str, Any]:
        audio = _decode_audio_base64(audio_base64)
        if not self.asr_state.configured:
            return self._asr_error("asr_unconfigured", "ASR 未配置，请先在 config.yaml 里配置语音识别。")
        if len(audio) > self.asr_state.max_bytes:
            return self._asr_error("audio_too_large", "录音太长了，请缩短后再试。")
        result = self.asr.transcribe(audio, mime_type)
        payload: dict[str, Any] = {
            "ok": result.ok,
            "transcript": result.transcript,
            "confidence": result.confidence,
            "provider": result.provider,
            "submitted": False,
        }
        if result.error:
            payload["error"] = result.error
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
        return {"ok": True, "submitted": True, "sequence": sequence, "events": [event.to_dict() for event in events]}

    def _run_serial(self, label: str, callback: Callable[[], list[AgentEvent]]) -> tuple[int, list[AgentEvent]]:
        with self._command_lock:
            self._command_sequence += 1
            sequence = self._command_sequence
            events = callback()
        return sequence, events

    @staticmethod
    def _asr_error(error: str, message: str) -> dict[str, Any]:
        return {"ok": False, "submitted": False, "transcript": "", "error": error, "message": message}

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
        payload: dict[str, Any] = {
            "workspace": str(self.workspace),
            "asr": {
                "enabled": self.asr_state.enabled,
                "configured": self.asr_state.configured,
                "provider": self.asr_state.provider,
                "max_seconds": self.asr_state.max_seconds,
                "max_bytes": self.asr_state.max_bytes,
                "error": self.asr_state.error,
            },
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
