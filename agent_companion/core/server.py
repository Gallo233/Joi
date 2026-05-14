from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.schemas import AgentEvent
from agent_companion.core.schemas import EventType
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
    def __init__(self, workspace: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.workspace = workspace.resolve()
        self.host = host
        self.port = port
        self.app = AgentCompanionApp(self.workspace)
        self.tts = TtsBridge(self.workspace)
        self.clients: set[Any] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue[AgentEvent] | None = None

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
                approved = bool(params.get("approved", False))
                if not text:
                    await websocket.send(self._result(request_id, {"ok": False, "error": "empty_text"}))
                    return
                asyncio.create_task(asyncio.to_thread(self.app.handle_user_text, text, approved))
                await websocket.send(self._result(request_id, {"ok": True, "submitted": True}))
                return
            if method == "approval.resolve":
                task_id = str(params.get("task_id") or "")
                approved = bool(params.get("approved", False))
                asyncio.create_task(asyncio.to_thread(self.app.resolve_approval, task_id, approved))
                await websocket.send(self._result(request_id, {"ok": True, "submitted": True}))
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
                    }
                )
            payload["character"] = {"name": character.name, "sprites": sprites}
        except Exception:
            return payload
        return payload

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> str:
        return json.dumps({"jsonrpc": "2.0", "id": request_id or f"server-{uuid.uuid4().hex[:8]}", "result": result}, ensure_ascii=False)

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> str:
        return json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}, ensure_ascii=False)


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
