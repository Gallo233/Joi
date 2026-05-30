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

from agent_companion.core.agent_cli import scan_agent_clis, test_agent_cli
from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.runtime_config_writer import preview_runtime_config_update
from agent_companion.core.schemas import AgentEvent, DisplayCard
from agent_companion.core.schemas import EventType
from agent_companion.core.runtime_status import build_runtime_status
from agent_companion.core.skill_manifest import build_native_skill_manifest
from agent_companion.core.speech_input import AsrRuntimeState, SpeechInputProvider, build_asr_provider
from agent_companion.core.tts_bridge import TtsBridge
from agent_companion.core.voice import safe_voice_line
from agent_companion.core.watch_commentary import WatchCommentaryPlanner
from agent_companion.core.watch_loop import WatchLoopController, WatchLoopOptions, WatchLoopTick


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
        self.watch_commentary = WatchCommentaryPlanner(self.workspace, self.app.character)
        self.watch_loop = WatchLoopController(self._watch_loop_tick, self.app.bus.emit)
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
                self.watch_loop.stop(emit=False)
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
            if method == "watch.loop.start":
                result = await asyncio.to_thread(self.watch_loop_start_command, params)
                await websocket.send(self._result(request_id, result))
                return
            if method == "watch.loop.stop":
                result = await asyncio.to_thread(self.watch_loop_stop_command)
                await websocket.send(self._result(request_id, result))
                return
            if method == "watch.loop.configure":
                result = await asyncio.to_thread(self.watch_loop_configure_command, params)
                await websocket.send(self._result(request_id, result))
                return
            if method == "watch.loop.refresh":
                result = await asyncio.to_thread(self.watch_loop_refresh_command, params)
                await websocket.send(self._result(request_id, result))
                return
            if method == "watch.loop.status":
                await websocket.send(self._result(request_id, self.watch_loop_status_command()))
                return
            if method == "background.status":
                await websocket.send(self._result(request_id, self.background_status_command()))
                return
            if method == "background.configure":
                result = await asyncio.to_thread(self.background_configure_command, params)
                await websocket.send(self._result(request_id, result))
                return
            if method == "background.clear":
                result = await asyncio.to_thread(self.background_clear_command)
                await websocket.send(self._result(request_id, result))
                return
            if method == "skills.list":
                await websocket.send(self._result(request_id, self.skill_manifest_command()))
                return
            if method == "agent_cli.list":
                result = await asyncio.to_thread(self.agent_cli_list_command)
                await websocket.send(self._result(request_id, result))
                return
            if method == "agent_cli.test":
                result = await asyncio.to_thread(self.agent_cli_test_command, params)
                await websocket.send(self._result(request_id, result))
                return
            if method == "audit.recent":
                limit = _safe_int(params.get("limit")) or 50
                await websocket.send(self._result(request_id, self.audit_recent_command(limit)))
                return
            if method == "memory.status":
                await websocket.send(self._result(request_id, self.memory_status_command()))
                return
            if method == "memory.recall":
                result = self.memory_recall_command(params)
                await websocket.send(self._result(request_id, result))
                return
            if method == "memory.browse_vault":
                result = self.memory_browse_vault_command()
                await websocket.send(self._result(request_id, result))
                return
            if method == "memory.save_candidate":
                result = self.memory_save_candidate_command(params)
                await websocket.send(self._result(request_id, result))
                return
            if method == "memory.reject_candidate":
                result = self.memory_reject_candidate_command(params)
                await websocket.send(self._result(request_id, result))
                return
            if method == "memory.set_enabled":
                result = self.memory_set_enabled_command(params)
                await websocket.send(self._result(request_id, result))
                return
            if method == "memory.delete":
                result = self.memory_delete_command(params)
                await websocket.send(self._result(request_id, result))
                return
            if method == "memory.clear":
                result = self.memory_clear_command()
                await websocket.send(self._result(request_id, result))
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
            "event_tool": str(event.agent_state.get("tool") or ""),
            "watch_commentary": bool(event.agent_state.get("watch_commentary")),
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
            submitted = self.submit_user_text(result.transcript)
            payload["submitted"] = True
            payload["sequence"] = submitted.get("sequence")
            payload["events"] = submitted.get("events", [])
            if "watch_loop" in submitted:
                payload["watch_loop"] = submitted["watch_loop"]
        return payload

    def submit_user_text(self, text: str) -> dict[str, Any]:
        if _looks_like_watch_loop_stop(text):
            return self.watch_loop_stop_command()
        sequence, events = self._run_serial("user.message", lambda: self.app.handle_user_text(text))
        payload: dict[str, Any] = {"ok": True, "submitted": True, "sequence": sequence, "events": [event.to_dict() for event in events]}
        if _looks_like_watch_loop_start(text):
            self.watch_commentary.reset()
            payload["watch_loop"] = self.watch_loop.start(self._watch_loop_options_from_params({"query": text})).to_agent_state()
        return payload

    def watch_loop_start_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.watch_commentary.reset()
        snapshot = self.watch_loop.start(self._watch_loop_options_from_params(params if isinstance(params, dict) else {}))
        return {"ok": True, "watch_loop": snapshot.to_agent_state()}

    def watch_loop_stop_command(self) -> dict[str, Any]:
        snapshot = self.watch_loop.stop()
        return {"ok": True, "watch_loop": snapshot.to_agent_state()}

    def watch_loop_configure_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.watch_loop.snapshot()
        merged: dict[str, Any] = {
            "query": current.query or "陪我看当前视频",
            "interval_seconds": current.interval_seconds,
            "sample_count": current.sample_count,
            "sample_interval_ms": 700,
            "transcript_source": current.transcript_source or "system_audio",
            "transcribe": True,
            "proactive_enabled": current.proactive_enabled,
            "commentary_interval_seconds": current.commentary_interval_seconds or 30.0,
            "vision_interval_ticks": current.vision_interval_ticks,
        }
        if isinstance(params, dict):
            merged.update(params)
        snapshot = self.watch_loop.configure(self._watch_loop_options_from_params(merged))
        if not snapshot.proactive_enabled:
            self.watch_commentary.reset()
        return {"ok": True, "watch_loop": snapshot.to_agent_state()}

    def watch_loop_refresh_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.watch_loop.snapshot()
        if not current.active:
            return {"ok": False, "error": "watch_loop_inactive", "watch_loop": current.to_agent_state()}
        force_visual = _safe_bool(params.get("force_visual_summary"), False) if isinstance(params, dict) else False
        snapshot = self.watch_loop.refresh(force_visual_summary=force_visual)
        return {"ok": True, "watch_loop": snapshot.to_agent_state()}

    def watch_loop_status_command(self) -> dict[str, Any]:
        return {"ok": True, "watch_loop": self.watch_loop.snapshot().to_agent_state()}

    def background_status_command(self) -> dict[str, Any]:
        return {"ok": True, "background": self.app.background_context.status()}

    def background_configure_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        enabled = params.get("enabled") if isinstance(params.get("enabled"), bool) else None
        result = self.app.background_context.configure(
            enabled=enabled,
            scope_type=str(params.get("scope_type") or ""),
            label=str(params.get("label") or ""),
            active_scope_id=str(params.get("active_scope_id") or ""),
        )
        self._emit_background_audit(
            "背景上下文设置已更新。" if result.get("ok") else "背景上下文设置没有更新。",
            result.get("background", {}),
            status="success" if result.get("ok") else "failed",
        )
        return result

    def background_clear_command(self) -> dict[str, Any]:
        result = self.app.background_context.clear_context()
        self._emit_background_audit("背景上下文摘要已清空。", result.get("background", {}), status="info")
        return result

    def memory_status_command(self) -> dict[str, Any]:
        return {"ok": True, "memory": self.app.memory.status()}

    def skill_manifest_command(self) -> dict[str, Any]:
        tts_status = self.tts.status_payload()
        return {
            "ok": True,
            "skills": build_native_skill_manifest(
                self.workspace,
                asr_state=self.asr_state,
                tts_status=tts_status,
                memory_status=self.app.memory.status(),
                skill_settings=self.app.skill_settings_payload(),
            ),
        }

    def agent_cli_list_command(self) -> dict[str, Any]:
        return scan_agent_clis()

    def agent_cli_test_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        cli_id = str(params.get("id") or "") if isinstance(params, dict) else ""
        return test_agent_cli(cli_id)

    def audit_recent_command(self, limit: int = 50) -> dict[str, Any]:
        return {"ok": True, "audit": self.app.audit_store.recent(limit)}

    def memory_recall_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = str(params.get("query") or "") if isinstance(params, dict) else ""
        limit = _safe_int(params.get("limit")) if isinstance(params, dict) else None
        safe_limit = min(20, max(1, int(limit or 8)))
        return {"ok": True, "memories": self.app.memory.recall(query, safe_limit), "memory": self.app.memory.status()}

    def memory_browse_vault_command(self) -> dict[str, Any]:
        return {"ok": True, "vault": self.app.memory.browse_vault(), "memory": self.app.memory.status()}

    def memory_save_candidate_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        candidate_id = _safe_int(params.get("candidate_id")) if isinstance(params, dict) else None
        if candidate_id is None:
            return {"ok": False, "error": "missing_candidate_id", "memory": self.app.memory.status()}
        result = self.app.memory.save_candidate(candidate_id)
        return {**result, "memory": self.app.memory.status()}

    def memory_reject_candidate_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        candidate_id = _safe_int(params.get("candidate_id")) if isinstance(params, dict) else None
        if candidate_id is None:
            return {"ok": False, "error": "missing_candidate_id", "memory": self.app.memory.status()}
        reason = str(params.get("reason") or "user_rejected") if isinstance(params, dict) else "user_rejected"
        result = self.app.memory.reject_candidate(candidate_id, reason)
        return {**result, "memory": self.app.memory.status()}

    def memory_set_enabled_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        enabled = _safe_bool(params.get("enabled"), True) if isinstance(params, dict) else True
        return {"ok": True, "memory": self.app.memory.set_enabled(enabled)}

    def memory_delete_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        memory_id = _safe_int(params.get("memory_id")) if isinstance(params, dict) else None
        if memory_id is None:
            return {"ok": False, "error": "missing_memory_id", "memory": self.app.memory.status()}
        result = self.app.memory.delete(memory_id)
        return {**result, "memory": self.app.memory.status()}

    def memory_clear_command(self) -> dict[str, Any]:
        result = self.app.memory.clear()
        return {**result, "memory": self.app.memory.status()}

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

    def _watch_loop_options_from_params(self, params: dict[str, Any]) -> WatchLoopOptions:
        query = str(params.get("query") or "陪我看当前视频").strip()
        sample_interval_ms = _safe_int(params.get("sample_interval_ms"))
        vision_interval_ticks = _safe_int(params.get("vision_interval_ticks"))
        return WatchLoopOptions(
            query=query or "陪我看当前视频",
            interval_seconds=_safe_float(params.get("interval_seconds"), 6.0),
            sample_count=_safe_int(params.get("sample_count")) or 3,
            sample_interval_ms=sample_interval_ms if sample_interval_ms is not None else 700,
            transcript_source=str(params.get("transcript_source") or "system_audio"),
            transcribe=bool(params.get("transcribe", True)),
            proactive_enabled=_safe_bool(params.get("proactive_enabled"), True),
            commentary_interval_seconds=_safe_float(params.get("commentary_interval_seconds"), 30.0),
            vision_interval_ticks=vision_interval_ticks if vision_interval_ticks is not None else 5,
        )

    def _watch_loop_tick(self, options: WatchLoopOptions) -> WatchLoopTick:
        next_iteration = self.watch_loop.snapshot().iterations + 1
        run_vision_summary = _watch_loop_should_summarize(options, next_iteration)
        with self._command_lock:
            result = self.app.refresh_watch_context(
                options.query,
                sample_count=options.sample_count,
                sample_interval_ms=options.sample_interval_ms,
                transcript_source=options.transcript_source,
                transcribe=options.transcribe,
                skip_summary=not run_vision_summary,
            )
        state = result.agent_state if isinstance(result.agent_state, dict) else {}
        transcript = state.get("transcript") if isinstance(state.get("transcript"), dict) else {}
        segments = transcript.get("segments") if isinstance(transcript.get("segments"), list) else []
        transcript_text: list[str] = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("text") or "").strip()
            if text:
                transcript_text.append(text[:180])
        error = str(transcript.get("error") or "")
        if not result.ok and not error:
            error = str(state.get("error") or "watch_loop_failed")
        visual_summary = str(state.get("sequence_summary") or state.get("vision_summary") or "")
        visual_status = str(state.get("model_status") or "")
        rolling = self.app.watch_session.transcript_state()
        source_health = rolling.get("source_health") if isinstance(rolling.get("source_health"), dict) else {}
        diagnostics = transcript.get("diagnostics") if isinstance(transcript.get("diagnostics"), dict) else {}
        if diagnostics:
            diagnostic_source = "system_audio" if diagnostics.get("capture") or diagnostics.get("audio_bytes") is not None else str(transcript.get("source") or options.transcript_source)
            source_health = dict(source_health)
            existing = source_health.get(diagnostic_source) if isinstance(source_health.get(diagnostic_source), dict) else {}
            source_health[diagnostic_source] = {
                **existing,
                "status": diagnostics.get("status") or transcript.get("status") or "unknown",
                "error": error,
                "capture": diagnostics.get("capture") or "",
                "audio_bytes": diagnostics.get("audio_bytes") or 0,
            }
        comment = self.watch_commentary.maybe_comment(rolling, min_interval_seconds=options.commentary_interval_seconds) if options.proactive_enabled else None
        tick = WatchLoopTick(
            ok=result.ok,
            summary=result.display_card.summary,
            transcript_text=transcript_text,
            transcript_source=str(transcript.get("source") or options.transcript_source),
            transcript_status=str(transcript.get("status") or ""),
            rolling_summary=str(rolling.get("summary") or ""),
            rolling_transcript=[str(text) for text in rolling.get("recent_text", []) if str(text).strip()],
            transcript_window_seconds=_safe_int(rolling.get("window_seconds")) or 0,
            source_health=source_health,
            proactive_reply=comment.reply if comment else "",
            proactive_voice_text=comment.voice_text if comment else "",
            proactive_emotion=comment.emotion if comment else "neutral",
            proactive_sprite=comment.sprite if comment else "1",
            proactive_reason=comment.reason if comment else "",
            visual_summary=visual_summary,
            visual_status=visual_status,
            error=error,
        )
        self.app.background_context.record_summary(
            tick.rolling_summary or tick.visual_summary or tick.summary,
            source="watch_loop",
            visual_status=tick.visual_status,
            transcript_source=tick.transcript_source,
        )
        return tick

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

    def _emit_background_audit(self, summary: str, background: dict[str, Any], *, status: str = "info") -> None:
        self.app.bus.emit(
            AgentEvent(
                EventType.AUDIT_EVENT,
                f"background-{uuid.uuid4().hex[:8]}",
                DisplayCard("背景伴随", summary, status=status),
                safe_voice_line("", fallback=""),
                {
                    "tool": "background.context",
                    "skill_id": "joi.watch",
                    "skill_category": "watch",
                    "skill_permission_level": "low",
                    "skill_audit": "background_context_audit",
                    "background_context": background,
                },
            )
        )

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
        memory_status = self.app.memory.status()
        payload: dict[str, Any] = {
            "workspace_label": self.workspace.name,
            "workspace_bound": True,
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
            "watch_loop": self.watch_loop.snapshot().to_agent_state(),
            "memory": memory_status,
            "audit": self.app.audit_store.status(),
            "background": self.app.background_context.status(),
            "skills": build_native_skill_manifest(
                self.workspace,
                asr_state=self.asr_state,
                tts_status=tts_status,
                memory_status=memory_status,
                skill_settings=self.app.skill_settings_payload(),
            ),
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
        self.watch_commentary.reload()
        self.app.reload_runtime_policy()

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


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "on", "enabled", "开启"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "关闭"}:
        return False
    return default


def _watch_loop_should_summarize(options: WatchLoopOptions, next_iteration: int) -> bool:
    interval = max(0, int(options.vision_interval_ticks or 0))
    if interval <= 0:
        return False
    if next_iteration <= 1:
        return True
    return next_iteration % interval == 0


def _looks_like_watch_loop_start(text: str) -> bool:
    value = " ".join((text or "").strip().split()).casefold()
    if not value:
        return False
    start_tokens = (
        "开始陪看",
        "持续陪看",
        "实时陪看",
        "陪我看",
        "陪着我看",
        "一起看",
        "边看边聊",
        "陪看这个视频",
    )
    if any(token in value for token in start_tokens):
        return True
    return ("这个视频" in value or "当前视频" in value or "正在播放" in value) and any(token in value for token in ("实时", "持续", "一直", "边看边"))


def _looks_like_watch_loop_stop(text: str) -> bool:
    value = " ".join((text or "").strip().split()).casefold()
    if not value:
        return False
    return any(
        token in value
        for token in (
            "停止陪看",
            "结束陪看",
            "关闭陪看",
            "停下陪看",
            "停止实时陪看",
            "别陪看了",
            "不用陪看了",
        )
    )


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
        "skills": {
            skill_id: {"enabled": bool(setting.enabled)}
            for skill_id, setting in sorted(config.skills.items())
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
