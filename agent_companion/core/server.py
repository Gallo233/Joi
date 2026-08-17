from __future__ import annotations

import argparse
import asyncio
import base64
import functools
import http.server
import json
import mimetypes
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable

from agent_companion.core.agent_cli import scan_agent_clis, test_agent_cli
from agent_companion.core.agent_skills import AgentSkillError, AgentSkillService
from agent_companion.core.app import AgentCompanionApp
from agent_companion.core.byok import ByokService
from agent_companion.core.character_packages import CharacterPackageError
from agent_companion.core.action_intent import ActionIntent, request_signals
from agent_companion.core.capability_orchestrator import ComputerUseOrchestrator
from agent_companion.core.run_coordinator import RunCoordinator
from agent_companion.core.run_journal import StoreRunJournal
from agent_companion.core.vision.target_evidence import TargetEvidence, evaluate_evidence
from agent_companion.core.collaboration_store import CollaborationStore, DEFAULT_PROJECT_ID, DEFAULT_THREAD_ID
from agent_companion.core.codex_support import codex_executable
from agent_companion.core.codex_runtime import CodexRuntimeSession
from agent_companion.core.coercion import bool_or, float_or, optional_int
from agent_companion.core.config import load_app_config, load_workspace_config
from agent_companion.core.language_policy import CHAT_LANGUAGE_CHOICES
from agent_companion.core.runtime_config_writer import preview_runtime_config_update
from agent_companion.core.rpc import (
    JsonRpcProtocolError,
    JsonRpcRouter,
    RpcMethodNotFound,
    encode_error,
    encode_result,
    parse_request,
)
from agent_companion.core.schemas import AgentEvent, DisplayCard
from agent_companion.core.schemas import EventType, RiskLevel, ToolRequest
from agent_companion.core.policy import PolicyDecision
from agent_companion.core.runtime_status import build_runtime_status
from agent_companion.core.scene_session import SceneSession
from agent_companion.core.game_adapters import GameAdapterRegistry
from agent_companion.core.minecraft_service import MinecraftGameService
from agent_companion.core.minecraft_screen import MinecraftScreenCache
from agent_companion.core.minecraft_autonomy import MinecraftAutonomyTicker
from agent_companion.core.platform_factory import get_screen_observer
from agent_companion.core.provider_client import chat_completion
from agent_companion.core.vision.ocr import PytesseractOcrExtractor
from agent_companion.core.services import ArtifactService, BackgroundContextService, MemoryService
from agent_companion.core.skill_manifest import build_native_skill_manifest
from agent_companion.core.realtime_voice import (
    ActionDispatchPermit,
    RealtimeVoiceCoordinator,
    RealtimeVoiceRuntimeState,
    build_realtime_voice_coordinator,
)
from agent_companion.core.speech_input import AsrRuntimeState, SpeechInputProvider, build_asr_provider
from agent_companion.core.tts_bridge import TtsBridge
from agent_companion.core.voice import safe_voice_line
from agent_companion.core.watch_commentary import WatchCommentaryPlanner
from agent_companion.core.watch_loop import WatchLoopController, WatchLoopOptions, WatchLoopTick


SPEAKABLE_EVENTS = {
    EventType.APPROVAL_REQUIRED,
    EventType.RUNTIME_FINAL,
    EventType.RUNTIME_ERROR,
    EventType.TOOL_COMPLETED,
    EventType.TOOL_FAILED,
}

JOI_CORE_PRODUCT = "joi-core"
JOI_CORE_PROTOCOL_VERSION = 1
# Joi's own proactive lines carry this run id so the autonomy speech guard can
# tell "the user is still talking" from "I just said something myself".
AUTONOMY_VOICE_RUN_ID = "minecraft-autonomy"
# How long a user turn is assumed to still own the voice channel.
AUTONOMY_SPEECH_HOLD_SECONDS = 12.0
MAX_VOICE_INPUT_GENERATIONS = 256


def _next_voice_audio_payload(stream: Any) -> dict[str, Any] | None:
    """`next()` wrapper whose StopIteration is safe to cross a Future."""

    try:
        return next(stream)
    except StopIteration:
        return None


class JsonRpcBridge:
    def __init__(
        self,
        workspace: Path,
        host: str = "127.0.0.1",
        port: int = 8765,
        asr_provider: SpeechInputProvider | None = None,
        asr_state: AsrRuntimeState | None = None,
        realtime_voice_coordinator: RealtimeVoiceCoordinator | None = None,
        realtime_voice_state: RealtimeVoiceRuntimeState | None = None,
        allow_mock_asr: bool = False,
        session_token: str = "",
        instance_id: str = "",
        ready_file: Path | None = None,
        parent_pid: int = 0,
    ) -> None:
        self.workspace = workspace.resolve()
        self.host = host
        self.port = port
        self.asset_port = port + 1
        self.session_token = session_token.strip()
        self.instance_id = instance_id.strip() or f"core-{uuid.uuid4().hex}"
        self.ready_file = ready_file.expanduser().resolve() if ready_file else None
        self.parent_pid = max(0, int(parent_pid or 0))
        self._asset_server: http.server.ThreadingHTTPServer | None = None
        self._asset_thread: threading.Thread | None = None
        self.app = AgentCompanionApp(self.workspace)
        self.collaboration = CollaborationStore(self.workspace, default_character_id=self.app.character.id)
        self.capability_orchestrator = ComputerUseOrchestrator(self.workspace, self.collaboration)
        self.agent_skills = AgentSkillService(self.workspace, self.collaboration)
        self.game_adapters = GameAdapterRegistry(self.workspace, self.collaboration.data_home)
        self.minecraft = MinecraftGameService(
            self.collaboration,
            self.game_adapters,
            screen_cache=MinecraftScreenCache(
                get_screen_observer(self.workspace),
                # The user's own OCR settings: a default-constructed extractor
                # ignored their language list and any custom tesseract path,
                # so screen reading silently produced nothing for them.
                _configured_ocr_extractor(self.workspace),
                summarizer=self.app._build_vision_summarizer(),
            ),
            plan_compiler=self._compile_plan_text,
        )
        self.game_adapters.set_minecraft_event_forwarder(self._on_minecraft_bridge_event)
        self.autonomy_enabled = False
        self.autonomy_interval_seconds = 30.0
        self.autonomy = MinecraftAutonomyTicker(
            propose=self._autonomy_propose,
            submit=self._autonomy_submit,
            session_context=self._autonomy_context,
            on_speak=self._autonomy_speak,
            speech_guard=self._autonomy_speech_guard,
        )
        self.app.set_session_authorizer(self._session_policy_decision)
        self.app.bus.set_context_provider(self.collaboration.context)
        self.app.bus.subscribe(self._record_collaboration_event)
        self.artifacts = ArtifactService(self.workspace)
        self.memory_service = MemoryService(self.app.memory)
        self.background_service = BackgroundContextService(
            self.app.background_context,
            lambda summary, background, status: self._emit_background_audit(
                summary,
                background,
                status=status,
            ),
        )
        self.codex_runtime = CodexRuntimeSession(self.workspace, self.app, self.app.bus.emit)
        self.tts = TtsBridge(self.workspace)
        self.watch_commentary = WatchCommentaryPlanner(self.workspace, self.app.character)
        self.scene_session = SceneSession()
        self.watch_loop = WatchLoopController(self._watch_loop_tick, self.app.bus.emit)
        if asr_provider is None:
            self.asr, self.asr_state = build_asr_provider(self.workspace, allow_mock=allow_mock_asr)
        else:
            self.asr = asr_provider
            self.asr_state = asr_state or AsrRuntimeState(True, True, "injected")
        if realtime_voice_coordinator is None:
            self.realtime_voice, self.realtime_voice_state = build_realtime_voice_coordinator(
                self.workspace,
                execute_action=self._execute_realtime_minecraft_action,
                cancel_action=self._cancel_realtime_minecraft_action,
                control_action=self._control_realtime_minecraft_action,
                validate_binding=self._realtime_minecraft_binding_ready,
                voice_locale=self.tts.voice_language,
                chat_locale=self.app.chat_language,
                persona=self._realtime_persona_prompt,
                world_memory=self.minecraft.world_memory,
                transcript_sink=self._persist_realtime_transcripts,
            )
        else:
            self.realtime_voice = realtime_voice_coordinator
            self.realtime_voice_state = realtime_voice_state or RealtimeVoiceRuntimeState(True, True, "injected")
        # An ASR request can outlive the intent that started it. Keep only a
        # bounded, in-memory generation marker per conversation so a late
        # transcript cannot become a new user command after the user typed,
        # cancelled, or switched conversations.
        self._voice_generation_lock = threading.Lock()
        self._voice_generations: dict[str, str] = {}
        self.clients: set[Any] = set()
        self._client_owners: dict[Any, str] = {}
        self._owner_clients: dict[str, Any] = {}
        self._realtime_epochs: dict[str, int] = {}
        self._tts_speaker_lock: asyncio.Lock | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue[AgentEvent] | None = None
        # Serialization is per conversation, not global -- see RunCoordinator.
        self.run_coordinator = RunCoordinator(self.collaboration.runs)
        self.app.set_run_journal(StoreRunJournal(self.collaboration.runs, self.run_coordinator, self.collaboration.context))
        self.app.set_session_provider(self._active_session_snapshot)
        self.agent_cli_takeover: dict[str, Any] = {
            "enabled": False,
            "mode": "byok",
            "selected": "codex",
            "model": "默认",
            "reasoning": "XHigh",
        }
        codex_available = bool(_codex_executable())
        self._joi_mcp_status: dict[str, Any] = {
            "connected": False,
            "available": codex_available,
            "status": "not_checked" if codex_available else "codex_not_found",
        }
        self.byok = ByokService(self.workspace, self._reload_runtime_after_config_change)
        self.rpc = self._build_rpc_router()

    async def serve(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("缺少 websockets 依赖，请先安装 requirements.txt。") from exc

        self.loop = asyncio.get_running_loop()
        self._tts_speaker_lock = asyncio.Lock()
        self.queue = asyncio.Queue()
        self.app.bus.subscribe(self._on_event)
        self._start_character_asset_server()
        try:
            async with websockets.serve(self._client_handler, self.host, self.port):
                print(f"Joi Core listening on ws://{self.host}:{self.port}")
                self._write_ready_file()
                pump = asyncio.create_task(self._event_pump())
                # A selected local voice loads weights in the background so
                # the first conversation turn does not pay the cold-start
                # cost. MiMo and disabled TTS return immediately here.
                self.tts.start_warmup()
                # Python's default SIGTERM handler stops the process outright,
                # so the cleanup below never runs and the local voice service
                # is left holding its weights. Turning the signal into an
                # ordinary wake-up is what makes "closing Joi closes it" true
                # however Joi was closed.
                stopping = self._install_stop_signals()
                try:
                    if self.parent_pid > 1:
                        await self._wait_for_parent_exit(stopping)
                    else:
                        await stopping.wait()
                finally:
                    self.watch_loop.stop(emit=False)
                    pump.cancel()
                    # Joi is closing, so the local voice service closes with
                    # it: it was started for this session and would otherwise
                    # hold its weights in memory until the next reboot.
                    self.tts.stop_local_service()
        finally:
            self.autonomy.stop_all()
            self.realtime_voice.shutdown()
            self.minecraft.shutdown()
            self._stop_character_asset_server()
            self._remove_ready_file()

    def _install_stop_signals(self) -> asyncio.Event:
        """Make a termination signal a normal shutdown rather than a stop.

        Only SIGTERM and SIGINT, and only when the loop can take handlers --
        on a platform or thread that cannot, the event simply never fires and
        behaviour is what it was before.
        """

        stopping = asyncio.Event()
        for name in ("SIGTERM", "SIGINT"):
            handler = getattr(signal, name, None)
            if handler is None:
                continue
            try:
                self.loop.add_signal_handler(handler, stopping.set)
            except (NotImplementedError, RuntimeError, ValueError):
                continue
        return stopping

    async def _wait_for_parent_exit(self, stopping: asyncio.Event | None = None) -> None:
        while _process_is_alive(self.parent_pid):
            if stopping is not None and stopping.is_set():
                return
            await asyncio.sleep(1.0)

    def _on_event(self, event: AgentEvent) -> None:
        if self.loop is None or self.queue is None:
            return
        self.loop.call_soon_threadsafe(self.queue.put_nowait, event)

    def _active_session_snapshot(self) -> dict[str, Any]:
        """The capability session the character's expression must respect."""
        session_id = str(self.collaboration.context().get("session_id") or "")
        return self.collaboration.session_payload(session_id, include_receipts=False) if session_id else {}

    def _session_policy_decision(self, request: ToolRequest, risk: RiskLevel) -> PolicyDecision | None:
        context = self.collaboration.context()
        session_id = str(context.get("session_id") or "")
        if not session_id:
            return None
        session = self.collaboration.session_payload(session_id, include_receipts=False)
        if not session:
            return None
        if session.get("state") != "running":
            return PolicyDecision(risk, False, True, "当前能力会话已暂停，请先继续或由你接管。")
        intent = ActionIntent.from_request(request)
        verdict = self.collaboration.action_allowed(
            session_id,
            request.name,
            risk.value,
            signals=_sensitive_request_signals(request),
            effect_kind=intent.effect_kind.value,
        )
        if verdict.get("reason") == "sensitive_action":
            label = _SENSITIVE_ACTION_LABELS.get(str(verdict.get("sensitive_action") or ""), "敏感操作")
            return PolicyDecision(risk, False, True, f"这一步看起来涉及{label}，始终需要这一次明确确认。")
        if not verdict.get("allowed"):
            if verdict.get("reason") == "delegate_launch_expired":
                return PolicyDecision(risk, False, True, "这次委托是给上一次启动的 Joi 的，重启后需要你再确认一次。")
            return PolicyDecision(risk, False, True, "观察模式不能改变外部状态，可切换到协作后继续。")
        profile = str(session.get("permission_profile") or "observe")
        if not verdict.get("scope_bound"):
            # delegate: bounded by this Joi launch rather than by project bindings.
            return PolicyDecision(risk, True, False, "委托模式在本次 Joi 启动期间持续执行。")
        permission = self.collaboration.permission_for_session(session_id)
        scope = permission.get("scope") if isinstance(permission.get("scope"), dict) else {}
        if not _request_within_bound_scope(request, scope, self.app.current_target_evidence()):
            return PolicyDecision(risk, False, True, "这一步超出项目绑定范围，需要确认扩权。")
        return PolicyDecision(risk, True, False, f"{profile} 模式在项目绑定范围内自动执行。")

    def _record_collaboration_event(self, event: AgentEvent) -> None:
        self.collaboration.record_event(event)
        state = event.agent_state if isinstance(event.agent_state, dict) else {}
        session_id = str(event.session_id or state.get("session_id") or self.collaboration.context().get("session_id") or "")
        tool = str(state.get("tool") or "")
        if not session_id or event.type not in {EventType.TOOL_COMPLETED, EventType.TOOL_FAILED}:
            return
        if not (tool.startswith("computer.") or tool.startswith("browser.") or tool.startswith("game.")):
            return
        self.capability_orchestrator.record_tool_event(session_id, event)

    async def _client_handler(self, websocket: Any) -> None:
        if self.session_token and _websocket_session_token(websocket) != self.session_token:
            await websocket.close(code=4401, reason="joi_core_auth_required")
            return
        owner_id = f"shell-{uuid.uuid4().hex}"
        self.clients.add(websocket)
        self._client_owners[websocket] = owner_id
        self._owner_clients[owner_id] = websocket
        try:
            ready_payload = await asyncio.to_thread(self._ready_payload)
            await websocket.send(
                json.dumps(
                    {"jsonrpc": "2.0", "method": "core.ready", "params": ready_payload},
                    ensure_ascii=False,
                )
            )
            async for raw in websocket:
                await self._handle_message(websocket, raw)
        except Exception:
            # Webviews can disappear without a WebSocket close frame when the
            # native window quits. Treat that as a normal client disconnect.
            return
        finally:
            await asyncio.to_thread(self.realtime_voice.stop_owner, owner_id, "transport_lost")
            self.clients.discard(websocket)
            self._client_owners.pop(websocket, None)
            self._owner_clients.pop(owner_id, None)

    async def _handle_message(self, websocket: Any, raw: str) -> None:
        request_id: Any = None
        try:
            request = parse_request(raw)
            request_id = request.request_id
            owner_id = self._client_owners.get(websocket, "")
            if request.method.startswith("voice.realtime."):
                result = await self._dispatch_realtime_voice(owner_id, request.method, request.params)
                await websocket.send(self._result(request_id, result))
            else:
                dispatched = await self.rpc.dispatch(request.method, request.params)
                await websocket.send(self._result(request_id, dispatched.result))
                if dispatched.broadcast_ready:
                    await self._broadcast_ready()
        except JsonRpcProtocolError as exc:
            await websocket.send(self._error(exc.request_id, exc.code, exc.message))
        except RpcMethodNotFound as exc:
            await websocket.send(self._error(request_id, -32601, f"unknown method: {exc.method}"))
        except Exception as exc:
            await websocket.send(self._error(request_id, -32603, str(exc)[:500]))

    def _build_rpc_router(self) -> JsonRpcRouter:
        router = JsonRpcRouter()
        router.register("core.livez", lambda _: self._health_payload(ready=False))
        router.register("core.readyz", lambda _: self._health_payload(ready=True))
        router.register("user.message", self._rpc_user_message)
        router.register("runtime.status", lambda _: self.runtime_status_command())
        router.register("runtime.configure", self.runtime_configure_command, run_in_thread=True, broadcast_ready=True)
        router.register("runtime.start", lambda _: self.runtime_start_command(), run_in_thread=True, broadcast_ready=True)
        router.register("runtime.stop", lambda _: self.runtime_stop_command(), run_in_thread=True, broadcast_ready=True)
        router.register(
            "runtime.approval.resolve",
            lambda params: self.runtime_approval_resolve_command(
                str(params.get("approval_id") or ""),
                bool(params.get("approved", False)),
            ),
            run_in_thread=True,
        )
        router.register("joi_mcp.status", lambda _: self.joi_mcp_status_command(), run_in_thread=True)
        router.register("joi_mcp.install_codex", lambda _: self.joi_mcp_install_codex_command(), run_in_thread=True, broadcast_ready=True)
        router.register("skill.run", self.skill_run_command, run_in_thread=True)
        router.register("skill.catalog", self.agent_skill_list_command)
        router.register("skill.inspect", self.agent_skill_inspect_command, run_in_thread=True)
        router.register("skill.install", self.agent_skill_install_command, run_in_thread=True, broadcast_ready=True)
        router.register("skill.update", self.agent_skill_update_command, run_in_thread=True, broadcast_ready=True)
        router.register("skill.uninstall", self.agent_skill_uninstall_command, run_in_thread=True, broadcast_ready=True)
        router.register("skill.validate", self.agent_skill_validate_command, run_in_thread=True)
        router.register("skill.enable", self.agent_skill_enable_command, run_in_thread=True, broadcast_ready=True)
        router.register("skill.draft.list", self.agent_skill_draft_list_command)
        router.register("skill.draft.create", self.agent_skill_draft_create_command, run_in_thread=True)
        router.register("skill.draft.approve", self.agent_skill_draft_approve_command, run_in_thread=True, broadcast_ready=True)
        router.register("skill.draft.reject", self.agent_skill_draft_reject_command, run_in_thread=True)
        router.register("watch.loop.start", self.watch_loop_start_command, run_in_thread=True)
        router.register("watch.loop.stop", lambda _: self.watch_loop_stop_command(), run_in_thread=True)
        router.register("watch.loop.configure", self.watch_loop_configure_command, run_in_thread=True)
        router.register("watch.loop.refresh", self.watch_loop_refresh_command, run_in_thread=True)
        router.register("watch.loop.status", lambda _: self.watch_loop_status_command())
        router.register("background.status", lambda _: self.background_status_command())
        router.register("background.configure", self.background_configure_command, run_in_thread=True)
        router.register("background.clear", lambda _: self.background_clear_command(), run_in_thread=True)
        router.register("skills.list", lambda _: self.skill_manifest_command())
        router.register("agent_cli.list", lambda _: self.agent_cli_list_command(), run_in_thread=True)
        router.register("agent_cli.test", self.agent_cli_test_command, run_in_thread=True)
        router.register("agent_cli.configure", self.agent_cli_configure_command, run_in_thread=True, broadcast_ready=True)
        router.register("agent_cli.status", lambda _: self.agent_cli_status_command())
        router.register("byok.status", lambda _: self.byok.status(), run_in_thread=True)
        router.register("byok.connect", self.byok_connect_command, run_in_thread=True, broadcast_ready=True)
        router.register("byok.test", lambda _: self.byok.test(), run_in_thread=True, broadcast_ready=True)
        router.register("byok.models", self.byok.discover_models, run_in_thread=True)
        router.register("byok.disconnect", lambda _: self.byok.disconnect(), run_in_thread=True, broadcast_ready=True)
        router.register("audit.recent", lambda params: self.audit_recent_command(optional_int(params.get("limit")) or 50))
        router.register("conversation.history", self.conversation_history_command)
        router.register("project.list", self.project_list_command)
        router.register("project.create", self.project_create_command, run_in_thread=True, broadcast_ready=True)
        router.register("project.update", self.project_update_command, run_in_thread=True, broadcast_ready=True)
        router.register("project.archive", self.project_archive_command, run_in_thread=True, broadcast_ready=True)
        router.register("project.delete", self.project_delete_command, run_in_thread=True, broadcast_ready=True)
        router.register("thread.list", self.thread_list_command)
        router.register("thread.create", self.thread_create_command, run_in_thread=True, broadcast_ready=True)
        router.register("thread.update", self.thread_update_command, run_in_thread=True, broadcast_ready=True)
        router.register("thread.activate", self.thread_activate_command, run_in_thread=True, broadcast_ready=True)
        router.register("thread.archive", self.thread_archive_command, run_in_thread=True, broadcast_ready=True)
        router.register("thread.delete", self.thread_delete_command, run_in_thread=True, broadcast_ready=True)
        router.register("resource_binding.list", self.resource_binding_list_command)
        router.register("resource_binding.add", self.resource_binding_add_command, run_in_thread=True, broadcast_ready=True)
        router.register("resource_binding.remove", self.resource_binding_remove_command, run_in_thread=True, broadcast_ready=True)
        router.register("capability.session.start", self.capability_session_start_command, run_in_thread=True, broadcast_ready=True)
        router.register("capability.session.status", self.capability_session_status_command)
        router.register("capability.session.pause", lambda params: self.capability_session_transition_command(params, "paused"), run_in_thread=True, broadcast_ready=True)
        router.register("capability.session.resume", lambda params: self.capability_session_transition_command(params, "running"), run_in_thread=True, broadcast_ready=True)
        router.register("capability.session.cancel", lambda params: self.capability_session_transition_command(params, "cancelled"), run_in_thread=True, broadcast_ready=True)
        router.register("permission.grant", self.permission_grant_command, run_in_thread=True, broadcast_ready=True)
        router.register("permission.expand", self.permission_expand_command, run_in_thread=True, broadcast_ready=True)
        router.register("permission.revoke", self.permission_revoke_command, run_in_thread=True, broadcast_ready=True)
        router.register("action_receipt.list", self.action_receipt_list_command)
        router.register("game.adapter.list", lambda _: self.game_adapter_list_command())
        router.register("game.adapter.status", self.game_adapter_status_command)
        router.register("game.adapter.install", self.game_adapter_install_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.uninstall", self.game_adapter_uninstall_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.enable", self.game_adapter_enable_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.run", self.game_adapter_run_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.minecraft.connection.status", lambda _: self.game_adapters.minecraft_connection_status())
        router.register("game.adapter.minecraft.connection.configure", self.game_adapter_minecraft_connection_configure_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.minecraft.autonomy.configure", self.game_adapter_autonomy_configure_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.minecraft.autonomy.status", self.game_adapter_autonomy_status_command)
        router.register("game.adapter.minecraft.snapshot", self.game_adapter_minecraft_snapshot_command)
        router.register("game.adapter.minecraft.plan.preview", self.game_adapter_plan_preview_command, run_in_thread=True)
        router.register("game.adapter.minecraft.plan.execute", self.game_adapter_plan_execute_command, run_in_thread=True)
        router.register("game.adapter.minecraft.plan.status", self.game_adapter_plan_status_command)
        router.register("game.adapter.minecraft.plan.cancel", self.game_adapter_plan_cancel_command, run_in_thread=True)
        router.register("game.adapter.pause", self.game_adapter_pause_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.resume", self.game_adapter_resume_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.session.start", self.game_adapter_session_start_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.session.status", self.game_adapter_session_status_command)
        router.register("game.adapter.session.stop", self.game_adapter_session_stop_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.goal.submit", self.game_adapter_goal_submit_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.goal.pause", self.game_adapter_goal_pause_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.goal.resume", self.game_adapter_goal_resume_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.goal.cancel", self.game_adapter_goal_cancel_command, run_in_thread=True, broadcast_ready=True)
        router.register("character.list", lambda _: self.character_list_command())
        router.register("character.detail", self.character_detail_command)
        router.register("character.create", self.character_create_command, run_in_thread=True, broadcast_ready=True)
        router.register("character.update", self.character_update_command, run_in_thread=True, broadcast_ready=True)
        router.register("character.inspect", self.character_inspect_command, run_in_thread=True)
        router.register("character.import", self.character_import_command, run_in_thread=True, broadcast_ready=True)
        router.register("character.export", self.character_export_command, run_in_thread=True)
        router.register("character.activate", self.character_activate_command, run_in_thread=True, broadcast_ready=True)
        router.register("character.set_locale", self.character_set_locale_command, run_in_thread=True, broadcast_ready=True)
        router.register("character.duplicate", self.character_duplicate_command, run_in_thread=True, broadcast_ready=True)
        router.register("character.uninstall", self.character_uninstall_command, run_in_thread=True, broadcast_ready=True)
        router.register("character.check_updates", self.character_check_updates_command, run_in_thread=True)
        router.register("character.install_update", self.character_install_update_command, run_in_thread=True, broadcast_ready=True)
        router.register("memory.status", lambda _: self.memory_status_command())
        router.register("memory.list", self.memory_list_command)
        router.register("memory.pending", self.memory_pending_command)
        router.register("memory.recall", self.memory_recall_command)
        router.register("memory.browse_vault", lambda _: self.memory_browse_vault_command())
        router.register("memory.save_candidate", self.memory_save_candidate_command)
        router.register("memory.reject_candidate", self.memory_reject_candidate_command)
        router.register("memory.set_enabled", self.memory_set_enabled_command)
        router.register("memory.delete", self.memory_delete_command)
        router.register("memory.update", self.memory_update_command)
        router.register("memory.clear", lambda _: self.memory_clear_command())
        router.register("approval.resolve", self._rpc_approval_resolve)
        router.register("runtime.config.preview", self._rpc_runtime_config_preview)
        router.register("runtime.config.apply", self._rpc_runtime_config_apply)
        router.register("semantic_target.select", self._rpc_semantic_target_select)
        router.register(
            "voice.transcribe",
            self._rpc_voice_transcribe,
            aliases=("audio.transcribe",),
        )
        router.register("voice.cancel", self._rpc_voice_cancel)
        router.register("artifact.read", self._rpc_artifact_read, run_in_thread=True)
        router.register("core.ping", lambda _: {"ok": True})
        return router

    async def _rpc_user_message(self, params: dict[str, Any]) -> dict[str, Any]:
        text = str(params.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "empty_text"}
        thread_id = str(params.get("thread_id") or "").strip()
        if thread_id:
            activation = self._activate_thread_context(thread_id)
            if not activation.get("ok"):
                return activation
        asyncio.create_task(asyncio.to_thread(self.submit_user_text, text))
        return {"ok": True, "submitted": True}

    async def _rpc_approval_resolve(self, params: dict[str, Any]) -> dict[str, Any]:
        approval_id = str(params.get("approval_id") or "")
        if not approval_id:
            return {"ok": False, "error": "missing_approval_id"}
        approved = bool(params.get("approved", False))
        asyncio.create_task(asyncio.to_thread(self.resolve_approval_command, approval_id, approved))
        return {"ok": True, "submitted": True}

    async def _rpc_semantic_target_select(self, params: dict[str, Any]) -> dict[str, Any]:
        selection_id = str(params.get("selection_id") or "").strip()
        rank = optional_int(params.get("rank"))
        if not selection_id:
            return {"ok": False, "error": "missing_selection_id"}
        if rank is None or rank < 1:
            return {"ok": False, "error": "invalid_rank"}
        asyncio.create_task(asyncio.to_thread(self.select_semantic_target_command, selection_id, rank))
        return {"ok": True, "submitted": True}

    def _rpc_runtime_config_preview(self, params: dict[str, Any]) -> dict[str, Any]:
        updates = params.get("updates")
        return self.preview_runtime_config_update_command(updates if isinstance(updates, dict) else {})

    def _rpc_runtime_config_apply(self, params: dict[str, Any]) -> dict[str, Any]:
        updates = params.get("updates")
        return self.apply_runtime_config_update_command(updates if isinstance(updates, dict) else {})

    async def _rpc_voice_transcribe(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return as soon as ASR finishes; never include the LLM turn latency.

        The previous RPC called ``submit_user_text`` before replying. That made
        the shell's “transcribing” spinner cover ASR *plus* planning, model
        generation, tools, and sometimes TTS. Besides feeling slow, it made it
        impossible to tell which stage was actually slow.
        """

        thread_id = str(params.get("thread_id") or "").strip()[:120]
        if not thread_id and hasattr(self, "collaboration"):
            thread_id = str(self.collaboration.context().get("thread_id") or "")[:120]
        generation_id = str(params.get("generation_id") or "").strip()[:120] or f"voice-{uuid.uuid4().hex[:12]}"
        self._set_voice_generation(thread_id, generation_id)

        payload = await asyncio.to_thread(
            self.transcribe_audio,
            str(params.get("audio_base64") or ""),
            str(params.get("mime_type") or ""),
        )
        payload["generation_id"] = generation_id

        if not self._voice_request_is_current(thread_id, generation_id):
            return {
                **payload,
                "submitted": False,
                "stale": True,
                "events": [],
            }
        if not payload.get("ok"):
            error = str(payload.get("error") or "asr_failed")
            message = str(payload.get("message") or _friendly_asr_message(error))
            failure = await asyncio.to_thread(self._asr_error, error, message)
            return {**failure, "generation_id": generation_id, "latency": payload.get("latency", {})}

        transcript = str(payload.get("transcript") or "")
        asyncio.create_task(
            asyncio.to_thread(self._submit_voice_transcript, transcript, thread_id, generation_id)
        )
        return {
            **payload,
            "submitted": True,
            "submission": "queued",
            "stale": False,
        }

    def _rpc_voice_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(params.get("thread_id") or "").strip()[:120]
        if not thread_id and hasattr(self, "collaboration"):
            thread_id = str(self.collaboration.context().get("thread_id") or "")[:120]
        with self._voice_generation_lock:
            previous = self._voice_generations.pop(thread_id or "__active__", "")
        return {"ok": True, "cancelled": bool(previous)}

    async def _dispatch_realtime_voice(
        self,
        owner_id: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch an owner-bound realtime call outside the general RPC router.

        The owner comes from the authenticated WebSocket connection, never from
        JavaScript parameters. This prevents one Joi window from observing or
        stopping another window's ephemeral microphone session.
        """

        if not owner_id:
            return {"ok": False, "error": "realtime_owner_required"}
        if method == "voice.realtime.session.start":
            allowed = {"mode", "minecraft_session_id", "disclosure_accepted"}
            if set(params) - allowed or params.get("disclosure_accepted") is not True:
                return {"ok": False, "error": "realtime_disclosure_required"}
            mode = str(params.get("mode") or "conversation")
            minecraft_session_id = str(params.get("minecraft_session_id") or "")
            if mode not in {"conversation", "minecraft"}:
                return {"ok": False, "error": "realtime_mode_invalid"}
            if mode == "conversation" and minecraft_session_id:
                return {"ok": False, "error": "realtime_binding_invalid"}

            def emit(payload: dict[str, Any]) -> None:
                self._schedule_realtime_event(owner_id, payload)

            return await asyncio.to_thread(
                self.realtime_voice.start,
                owner_id,
                emit,
                mode=mode,
                minecraft_session_id=minecraft_session_id,
            )
        if method == "voice.realtime.audio.append":
            return await asyncio.to_thread(self.realtime_voice.append_audio, owner_id, params)
        if method == "voice.realtime.session.stop":
            if set(params) != {"session_id"}:
                return {"ok": False, "error": "realtime_stop_envelope_invalid"}
            session_id = str(params.get("session_id") or "")
            result = await asyncio.to_thread(self.realtime_voice.stop, owner_id, session_id)
            self._realtime_epochs.pop(session_id, None)
            return result
        if method == "voice.realtime.session.status":
            if set(params) - {"session_id"}:
                return {"ok": False, "error": "realtime_status_envelope_invalid"}
            return self.realtime_voice.status(owner_id, str(params.get("session_id") or ""))
        if method == "voice.realtime.game.control":
            if set(params) != {"session_id", "action"}:
                return {"ok": False, "error": "realtime_game_control_invalid"}
            return await asyncio.to_thread(
                self.realtime_voice.control,
                owner_id,
                str(params.get("session_id") or ""),
                str(params.get("action") or ""),
            )
        raise RpcMethodNotFound(method)

    def _schedule_realtime_event(self, owner_id: str, payload: dict[str, Any]) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            return

        def schedule() -> None:
            asyncio.create_task(self._handle_realtime_event(owner_id, payload))

        loop.call_soon_threadsafe(schedule)

    async def _handle_realtime_event(self, owner_id: str, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("type") or "")
        session_id = str(payload.get("session_id") or "")
        epoch = max(0, int(payload.get("epoch") or 0))
        if event_type == "barge_in":
            self._realtime_epochs[session_id] = max(self._realtime_epochs.get(session_id, 0), epoch)
            context = self.collaboration.context()
            self.app.voice_generations.retire(str(context.get("thread_id") or ""), "superseded")
        await self._send_to_owner(
            owner_id,
            json.dumps(
                {"jsonrpc": "2.0", "method": "voice.realtime.event", "params": payload},
                ensure_ascii=False,
            ),
        )
        if event_type == "assistant_text":
            await self._synthesize_realtime_text(owner_id, payload)

    def _execute_realtime_minecraft_action(
        self,
        session_id: str,
        goal_id: str,
        envelope: dict[str, Any],
        permit: ActionDispatchPermit,
    ) -> dict[str, Any]:
        return self.minecraft.submit_goal(
            {"session_id": session_id, "goal_id": goal_id, **envelope},
            cancel_requested=permit.cancel_requested.is_set,
            on_registered=permit.registered.set,
            on_submitted=permit.submitted.set,
        )

    def _cancel_realtime_minecraft_action(self, session_id: str, goal_id: str) -> dict[str, Any]:
        return self.minecraft.cancel({"session_id": session_id, "goal_id": goal_id})

    def _control_realtime_minecraft_action(self, action: str, session_id: str, goal_id: str) -> dict[str, Any]:
        operation = {
            "pause": self.minecraft.pause,
            "resume": self.minecraft.resume,
            "cancel": self.minecraft.cancel,
        }.get(action)
        return operation({"session_id": session_id, "goal_id": goal_id}) if operation else {"ok": False}

    def _realtime_minecraft_binding_ready(self, session_id: str) -> bool:
        status = self.minecraft.status({"session_id": session_id})
        session = status.get("session") if isinstance(status.get("session"), dict) else {}
        return bool(status.get("ok") and status.get("bridge_state") == "ready" and session.get("state") == "running")

    def _realtime_persona_prompt(self) -> str:
        """The active character harness, read per realtime session.

        A missing or broken harness leaves the generic Joi identity; the
        realtime call must never fail because a character package changed.
        """

        character = getattr(self.app, "character", None)
        if character is None:
            return ""
        try:
            return str(character.prompt_header() or "")
        except Exception:
            return ""

    def _on_minecraft_bridge_event(self, session_id: str, event: dict[str, Any]) -> None:
        """Route whitelisted chat lines from the bridge reader thread.

        handle_chat spawns its own worker for compile + submit, so this
        callback never blocks the event pump.
        """

        if str(event.get("type") or "") != "chat.observed":
            return
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        self.minecraft.handle_chat(
            session_id,
            str(payload.get("player") or ""),
            str(payload.get("text") or ""),
        )

    def _compile_plan_text(self, prompt: str) -> str:
        """Compile natural-language Minecraft goals into strict JSON plans."""

        if os.environ.get("AGENT_COMPANION_DISABLE_LLM") == "1":
            return "{}"
        try:
            config = load_app_config(self.workspace / "config.yaml")
        except Exception:
            return "{}"
        if config.llm.use_mock or not (config.llm.is_configured or config.llm.is_expression_configured):
            return "{}"
        try:
            outcome = chat_completion(
                config.llm,
                "fast",
                temperature=min(max(config.llm.temperature, 0.1), 0.6),
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "只输出严格的 JSON 对象，不要输出 JSON 以外的任何文本。"},
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception:
            return "{}"
        if not outcome.ok:
            return "{}"
        return str(outcome.value or "{}")

    def _autonomy_submit(self, session_id: str, intent: dict[str, Any]) -> dict[str, Any]:
        goal_id = f"autonomy-goal-{uuid.uuid4().hex}"
        return self.minecraft.submit_goal(
            {"session_id": session_id, "goal_id": goal_id, "final": True, "source": "voice", "intent": intent},
            autonomy=True,
        )

    def _autonomy_context(self, session_id: str) -> dict[str, Any]:
        status = self.minecraft.status({"session_id": session_id})
        session = status.get("session") if isinstance(status.get("session"), dict) else {}
        bridge = self.game_adapters.minecraft_session_status(session_id)
        if not status.get("ok") or bridge.get("active_goal") or session.get("state") != "running":
            return {"ok": False}
        snapshot = self.game_adapters.minecraft_snapshot(session_id)
        observation = snapshot.get("observation") if isinstance(snapshot.get("observation"), dict) else {}
        screen = self.minecraft.screen_cache.cached() if self.minecraft.screen_cache is not None else {}
        # Bridge event payloads stay inside Core: only sanitized types and the
        # observation projection reach the prompt (and never coordinates).
        recent_event_types = [str(event.get("type") or "") for event in self.game_adapters.minecraft_session_events(session_id)[-8:]]
        return {
            "ok": True,
            "observation": observation,
            "hostiles": list(observation.get("nearby_hostiles") or []),
            "recent_event_types": recent_event_types,
            "screen_text": str(screen.get("text") or "") if screen.get("ok") else "",
            "persona": self._realtime_persona_prompt(),
            "memory": self.minecraft.world_memory(session_id),
        }

    def _persist_realtime_transcripts(self, session_id: str, pairs: list[tuple[str, str]]) -> None:
        """M1: write the sanitized realtime exchange into conversation history.

        Only the bounded text pairs are stored - raw audio, provider IDs and
        coordinates never reach history. A failed write is silent: realtime
        stays usable without persistence.
        """

        try:
            for user_text, assistant_text in pairs[-40:]:
                user_text = " ".join(str(user_text).split())[:2000]
                assistant_text = " ".join(str(assistant_text).split())[:2000]
                if user_text:
                    self.collaboration.record_event(
                        {"type": "user_message", "agent_state": {"text": user_text, "source": "voice.realtime"}}
                    )
                if assistant_text:
                    self.collaboration.record_event(
                        {"type": "assistant_message", "agent_state": {"text": assistant_text, "source": "voice.realtime"}}
                    )
        except Exception:
            return

    def _autonomy_speech_guard(self) -> bool:
        """True while the user's own turn still owns the voice channel.

        A generation is only retired on barge-in, cancel or a character switch,
        so "a generation exists" is true forever after the first turn and would
        silence proactive speech for the rest of the session. What matters is
        whether a *recent* turn that autonomy did not start is still speaking.
        """

        thread_id = str(self.collaboration.context().get("thread_id") or "")
        current = self.app.voice_generations.current(thread_id)
        if current is None or current.run_id == AUTONOMY_VOICE_RUN_ID:
            return False
        return (time.time() - float(current.created_at or 0)) < AUTONOMY_SPEECH_HOLD_SECONDS

    def _autonomy_speak(self, text: str) -> None:
        """Speak one proactive line through the normal voice pipeline.

        The line still passes safe_voice_line, the speaker lock and the
        generation gate: no coordinates, no JSON, no talking over the user's
        own turn.
        """

        thread_id = str(self.collaboration.context().get("thread_id") or "")
        generation = self.app.voice_generations.begin(
            thread_id,
            character_id=str(self.app.character.id),
            # Marks the line as Joi's own initiative, so the speech guard does
            # not mistake it for a user turn and silence every line after it.
            run_id=AUTONOMY_VOICE_RUN_ID,
        )
        self.app.bus.emit(
            AgentEvent(
                EventType.TOOL_COMPLETED,
                f"minecraft-autonomy-{uuid.uuid4().hex[:8]}",
                # "对话" is what marks an assistant event as speech rather than
                # work: without it a proactive line renders as a task card.
                DisplayCard("对话", text[:200], status="success"),
                safe_voice_line(text, fallback=""),
                {
                    "tool": "minecraft.autonomy",
                    "proactive": True,
                    "voice_generation": generation.generation_id,
                    "skill_id": "joi.minecraft",
                    "skill_category": "game",
                    "skill_permission_level": "low",
                },
            )
        )

    def _autonomy_propose(self, prompt: str) -> dict[str, Any] | str:
        """One bounded text completion deciding speak/propose/none.

        Disabled LLM, mock or unconfigured providers degrade to {"kind":
        "none"}: autonomy is optional, silence is the right failure.
        """

        if os.environ.get("AGENT_COMPANION_DISABLE_LLM") == "1":
            return {"kind": "none"}
        try:
            config = load_app_config(self.workspace / "config.yaml")
        except Exception:
            return {"kind": "none"}
        if config.llm.use_mock or not (config.llm.is_configured or config.llm.is_expression_configured):
            return {"kind": "none"}
        try:
            outcome = chat_completion(
                config.llm,
                "voice_style",
                temperature=min(max(config.llm.temperature, 0.2), 0.85),
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "只输出严格的 JSON 对象，不要输出 JSON 以外的任何文本。"},
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception:
            return {"kind": "none"}
        if not outcome.ok:
            return {"kind": "none"}
        return str(outcome.value or "{}")

    def _rpc_artifact_read(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.read_artifact_command(str(params.get("artifact") or ""))

    def conversation_history_command(self, params: dict[str, Any]) -> dict[str, Any]:
        limit = optional_int(params.get("limit")) or 160
        after_sequence = optional_int(params.get("after_sequence")) or 0
        thread_id = str(params.get("thread_id") or self.collaboration.context()["thread_id"])
        rows = self.collaboration.history(thread_id, limit=limit, after_sequence=after_sequence)
        return {
            "ok": True,
            "events": rows,
            "latest_sequence": self.app.bus.latest_sequence,
            "active_approval_ids": self._active_approval_ids(),
            "thread_id": thread_id,
        }

    def project_list_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return {"ok": True, "projects": self.collaboration.list_projects(bool(params.get("include_archived"))), "active": self.collaboration.context()}

    def project_create_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        result = self.collaboration.create_project(str(params.get("name") or ""), str(params.get("default_character_id") or self.app.character.id))
        return {"ok": True, **result, "collaboration": self.collaboration.snapshot()}

    def project_update_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return self.collaboration.update_project(
            str(params.get("project_id") or ""),
            name=str(params["name"]) if "name" in params else None,
            default_character_id=str(params["default_character_id"]) if "default_character_id" in params else None,
            archived=bool(params["archived"]) if "archived" in params else None,
        )

    def project_archive_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return self.collaboration.update_project(str(params.get("project_id") or ""), archived=bool(params.get("archived", True)))

    def project_delete_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return self.collaboration.delete_project(str(params.get("project_id") or ""), bool(params.get("confirmed")))

    def thread_list_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        project_id = str(params.get("project_id") or self.collaboration.context()["project_id"])
        return {
            "ok": True,
            "threads": self.collaboration.list_threads(project_id, bool(params.get("include_archived")), str(params.get("query") or "")),
            "active": self.collaboration.context(),
        }

    def thread_create_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        project_id = str(params.get("project_id") or self.collaboration.context()["project_id"])
        result = self.collaboration.create_thread(project_id, str(params.get("title") or ""), str(params.get("character_id") or ""))
        if result.get("ok"):
            self._activate_thread_character(str((result.get("thread") or {}).get("character_id") or ""))
        return {**result, "collaboration": self.collaboration.snapshot()}

    def thread_update_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return self.collaboration.update_thread(
            str(params.get("thread_id") or ""),
            title=str(params["title"]) if "title" in params else None,
            character_id=str(params["character_id"]) if "character_id" in params else None,
            archived=bool(params["archived"]) if "archived" in params else None,
        )

    def thread_activate_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._activate_thread_context(str((params or {}).get("thread_id") or ""))

    def thread_archive_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return self.collaboration.update_thread(str(params.get("thread_id") or ""), archived=bool(params.get("archived", True)))

    def thread_delete_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return self.collaboration.delete_thread(str(params.get("thread_id") or ""), bool(params.get("confirmed")))

    def resource_binding_list_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        project_id = str((params or {}).get("project_id") or self.collaboration.context()["project_id"])
        return {"ok": True, "bindings": self.collaboration.list_bindings(project_id)}

    def resource_binding_add_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return self.collaboration.add_binding(
            str(params.get("project_id") or self.collaboration.context()["project_id"]),
            str(params.get("kind") or ""),
            str(params.get("value") or ""),
            str(params.get("label") or ""),
            params.get("metadata") if isinstance(params.get("metadata"), dict) else {},
        )

    def resource_binding_remove_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.collaboration.remove_binding(str((params or {}).get("binding_id") or ""))

    def capability_session_start_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        drivers = self.capability_orchestrator.driver_inventory(str(params.get("driver") or "auto"))
        result = self.collaboration.start_session(
            str(params.get("capability") or "computer_use"),
            str(params.get("goal") or ""),
            str(params.get("permission_profile") or "observe"),
            project_id=str(params.get("project_id") or ""),
            thread_id=str(params.get("thread_id") or ""),
            driver=drivers.selected,
            budget=params.get("budget") if isinstance(params.get("budget"), dict) else {},
            stop_conditions=params.get("stop_conditions") if isinstance(params.get("stop_conditions"), list) else [],
        )
        session = result.get("session") if isinstance(result.get("session"), dict) else {}
        if result.get("ok") and str(session.get("capability") or "") == "computer_use":
            self.app.set_computer_driver(drivers.selected, str(session.get("id") or ""))
        result["drivers"] = drivers.payload()
        return result

    def capability_session_status_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        session_id = str((params or {}).get("session_id") or self.collaboration.context().get("session_id") or "")
        session = self.collaboration.session_payload(session_id) if session_id else {}
        if session:
            session["orchestrator"] = self.capability_orchestrator.runtime_payload(session_id)
        return {"ok": bool(session_id), "session": session}

    def capability_session_transition_command(self, params: dict[str, Any] | None, state: str) -> dict[str, Any]:
        session_id = str((params or {}).get("session_id") or self.collaboration.context().get("session_id") or "")
        result = self.collaboration.transition_session(session_id, state)
        if state in {"cancelled", "paused"}:
            # Stopping the work stops the commentary about it; audio already in
            # synthesis for this turn is discarded rather than played after.
            reason = "cancelled" if state == "cancelled" else "taken_over"
            self.app.voice_generations.retire(str(self.collaboration.context().get("thread_id") or ""), reason)
        if state in {"cancelled", "completed", "failed"}:
            self.app.set_computer_driver("native")
        return result

    def permission_grant_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return self.collaboration.grant_permission(
            str(params.get("session_id") or self.collaboration.context().get("session_id") or ""),
            str(params.get("profile") or "observe"),
            params.get("scope") if isinstance(params.get("scope"), dict) else {},
        )

    def permission_expand_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        session_id = str(params.get("session_id") or self.collaboration.context().get("session_id") or "")
        result = self.collaboration.expand_permission(
            session_id,
            params.get("scope") if isinstance(params.get("scope"), dict) else {},
            confirmed=bool(params.get("confirmed")),
        )
        if result.get("changed"):
            additions = result.get("additions") if isinstance(result.get("additions"), dict) else {}
            self.app.bus.emit(
                AgentEvent(
                    EventType.AUDIT_EVENT,
                    f"permission-expand-{uuid.uuid4().hex[:8]}",
                    DisplayCard("权限范围已扩大", _scope_additions_summary(additions), status="info"),
                    safe_voice_line("", fallback=""),
                    {
                        "tool": "permission.expand",
                        "skill_permission_level": "high",
                        "skill_audit": "permission_expand_audit",
                        "session_id": session_id,
                        "permission_expand": additions,
                    },
                    session_id=session_id,
                )
            )
        return result

    def permission_revoke_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.collaboration.revoke_permission(str((params or {}).get("session_id") or self.collaboration.context().get("session_id") or ""))

    def action_receipt_list_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        session_id = str((params or {}).get("session_id") or self.collaboration.context().get("session_id") or "")
        return {"ok": bool(session_id), "receipts": self.collaboration.list_receipts(session_id) if session_id else []}

    def game_adapter_list_command(self) -> dict[str, Any]:
        return self.game_adapters.list()

    def game_adapter_status_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.game_adapters.status(str((params or {}).get("adapter_id") or ""))

    def game_adapter_install_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return self.game_adapters.install(str(params.get("adapter_id") or ""), confirmed=bool(params.get("confirmed")))

    def game_adapter_uninstall_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return self.game_adapters.uninstall(str(params.get("adapter_id") or ""), confirmed=bool(params.get("confirmed")))

    def game_adapter_enable_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return self.game_adapters.set_enabled(str(params.get("adapter_id") or ""), bool(params.get("enabled", True)))

    def game_adapter_minecraft_connection_configure_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.game_adapters.configure_minecraft_connection(params if isinstance(params, dict) else {})

    def game_adapter_run_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        adapter_id = str(params.get("adapter_id") or "")
        mode = str(params.get("mode") or "takeover")
        goal = str(params.get("goal") or "")
        dry_run = bool(params.get("dry_run", True))
        if dry_run:
            return self.game_adapters.prepare(adapter_id, mode, goal, True)
        if adapter_id == "minecraft":
            return {
                "ok": False,
                "error": "persistent_session_rpc_required",
                "zero_actions": True,
                "start_method": "game.adapter.session.start",
                "goal_method": "game.adapter.goal.submit",
            }
        session_result = self.collaboration.start_session(
            "game",
            goal,
            "collaborate",
            driver="structured_bridge" if mode == "companion" else self.capability_orchestrator.driver_inventory("auto").selected,
            budget=params.get("budget") if isinstance(params.get("budget"), dict) else {},
            stop_conditions=["user_input", "game_disconnect", "budget_exhausted"],
        )
        if not session_result.get("ok"):
            return session_result
        session = session_result.get("session") or {}
        prepared = self.game_adapters.prepare(adapter_id, mode, goal, False)
        tool = str(prepared.get("tool") or "")
        if tool:
            sequence, events = self._run_serial(
                "game.adapter.run",
                lambda: self.app.handle_skill_run(tool, prepared.get("arguments") if isinstance(prepared.get("arguments"), dict) else {}, user_text=goal or adapter_id),
            )
            ok = not any(event.type == EventType.TASK_FAILED for event in events)
            self.collaboration.transition_session(str(session.get("id") or ""), "completed" if ok else "failed")
            return {"ok": ok, "session": self.collaboration.session_payload(str(session.get("id") or "")), "prepared": prepared, "sequence": sequence, "events": [event.to_dict() for event in events]}
        if not prepared.get("ok"):
            self.collaboration.transition_session(str(session.get("id") or ""), "failed")
        elif mode == "companion":
            self.collaboration.transition_session(str(session.get("id") or ""), "completed")
        return {**prepared, "session": self.collaboration.session_payload(str(session.get("id") or ""))}

    def game_adapter_pause_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        adapter_id = str((params or {}).get("adapter_id") or "")
        if adapter_id == "minecraft":
            return self.minecraft.pause(params)
        result = self.game_adapters.pause(adapter_id)
        session_id = str((params or {}).get("session_id") or self.collaboration.context().get("session_id") or "")
        if session_id:
            result["session"] = self.collaboration.transition_session(session_id, "paused").get("session") or {}
        return result

    def game_adapter_resume_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        adapter_id = str((params or {}).get("adapter_id") or "")
        if adapter_id == "minecraft":
            return self.minecraft.resume(params)
        result = self.game_adapters.resume(adapter_id)
        session_id = str((params or {}).get("session_id") or self.collaboration.context().get("session_id") or "")
        if session_id:
            result["session"] = self.collaboration.transition_session(session_id, "running").get("session") or {}
        return result

    def game_adapter_session_start_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        if str(params.get("adapter_id") or "minecraft") != "minecraft":
            return {"ok": False, "error": "persistent_session_not_supported"}
        result = self.minecraft.start_session(params)
        if result.get("ok") and self.autonomy_enabled:
            session_id = str((result.get("session") or {}).get("id") or "")
            if session_id:
                self.autonomy.start(session_id)
        return result

    def game_adapter_session_status_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.minecraft.status(params)

    def game_adapter_session_stop_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        session_id = str(params.get("session_id") or "")
        if session_id:
            self.autonomy.stop(session_id)
        return self.minecraft.stop_session(params)

    def game_adapter_autonomy_configure_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        if set(params) - {"enabled", "interval_seconds"}:
            return {"ok": False, "error": "autonomy_configure_invalid"}
        if "enabled" in params:
            self.autonomy_enabled = bool(params.get("enabled"))
        if "interval_seconds" in params:
            try:
                self.autonomy_interval_seconds = float(params.get("interval_seconds"))
            except (TypeError, ValueError):
                return {"ok": False, "error": "autonomy_configure_invalid"}
            self.autonomy.set_interval(self.autonomy_interval_seconds)
        for session_id in self.game_adapters.minecraft_active_sessions():
            if self.autonomy_enabled:
                self.autonomy.start(session_id)
            else:
                self.autonomy.stop(session_id)
        return self.game_adapter_autonomy_status_command()

    def game_adapter_autonomy_status_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "enabled": self.autonomy_enabled,
            "interval_seconds": self.autonomy.interval_seconds,
            "sessions": {
                session_id: self.autonomy.status(session_id)
                for session_id in self.game_adapters.minecraft_active_sessions()
            },
        }

    def game_adapter_minecraft_snapshot_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        session_id = str((params or {}).get("session_id") or "")
        if not session_id:
            return {"ok": False, "error": "session_id_required"}
        return self.game_adapters.minecraft_snapshot(session_id)

    def game_adapter_plan_preview_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.minecraft.plan(params)

    def game_adapter_plan_execute_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.minecraft.execute_plan(params)

    def game_adapter_plan_status_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.minecraft.plan_status(params)

    def game_adapter_plan_cancel_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.minecraft.plan_cancel(params)

    def game_adapter_goal_submit_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.minecraft.submit_goal(params)

    def game_adapter_goal_pause_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.minecraft.pause(params)

    def game_adapter_goal_resume_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.minecraft.resume(params)

    def game_adapter_goal_cancel_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.minecraft.cancel(params)

    def character_list_command(self) -> dict[str, Any]:
        result = self._character_command(self.app.character_packages.list)
        for character in result.get("characters") or []:
            self._attach_character_image(character, "avatar_path", "avatar_url", "avatar_data_url")
            self._attach_character_image(character, "portrait_path", "portrait_url", "portrait_data_url")
        return result

    def character_detail_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        character_id = str((params or {}).get("character_id") or "")
        result = self._character_command(lambda: self.app.character_packages.detail(character_id))
        character = result.get("character") if isinstance(result.get("character"), dict) else {}
        self._attach_character_image(character, "avatar_path", "avatar_url", "avatar_data_url")
        self._attach_character_image(character, "portrait_path", "portrait_url", "portrait_data_url")
        return result

    def character_create_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = (params or {}).get("character")
        return self._character_preview_result(
            self._character_command(lambda: self.app.character_packages.create(payload if isinstance(payload, dict) else {}))
        )

    def character_update_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        character_id = str(params.get("character_id") or "")
        payload = params.get("character")
        return self._character_preview_result(
            self._character_command(lambda: self.app.character_packages.update(character_id, payload if isinstance(payload, dict) else {}))
        )

    def character_import_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        source = str((params or {}).get("path") or "")
        return self._character_command(lambda: self.app.character_packages.import_package(source))

    def character_inspect_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        source = str((params or {}).get("path") or "")
        return self._character_command(lambda: self.app.character_packages.inspect_package(source))

    def character_export_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        character_id = str(params.get("character_id") or "")
        destination = str(params.get("destination") or "")
        return self._character_command(lambda: self.app.character_packages.export_package(character_id, destination))

    def character_activate_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        character_id = str((params or {}).get("character_id") or "")

        def activate() -> dict[str, Any]:
            result = self.app.character_packages.activate(character_id)
            self._reload_active_character()
            # A different character must not finish the previous one's sentence.
            self.app.voice_generations.retire_for_character_change(character_id)
            result["thread"] = self._switch_to_character_thread(character_id, inherit=bool((params or {}).get("inherit_conversation")))
            result["memory"] = self.app.memory.status()
            result["ready"] = self._ready_payload()
            return result

        return self._character_preview_result(self._character_command(activate))

    def character_set_locale_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        character_id = str((params or {}).get("character_id") or "")
        locale = str((params or {}).get("locale") or "")

        def switch() -> dict[str, Any]:
            result = self.app.character_packages.set_locale(character_id, locale)
            # The persona, the greeting and the voice description all change
            # with the language, and every one of them is read at reload.
            self._reload_active_character()
            # A line already being spoken is in the language the user just
            # switched away from, so it is retired the same way a character
            # change retires it.
            self.app.voice_generations.retire_for_character_change(result["character_id"])
            result["ready"] = self._ready_payload()
            return result

        return self._character_preview_result(self._character_command(switch))

    def _switch_to_character_thread(self, character_id: str, *, inherit: bool = False) -> dict[str, Any]:
        """Move the conversation to the one belonging to this character.

        Switching used to relabel the current thread with the new character's
        id, which left every previous turn in place: the new character
        inherited the last one's conversation, and answered as if it had been
        there for it. The shell cleared its event list, so it looked separate
        while the model was still reading the old transcript.

        Each character gets its own thread instead. Inheriting is possible but
        has to be asked for, and copies nothing -- it keeps the caller on the
        current thread and moves its ownership, which is the old behaviour made
        explicit.
        """

        context = self.collaboration.context()
        project_id = context.get("project_id") or DEFAULT_PROJECT_ID
        current_thread_id = context.get("thread_id") or ""

        if inherit:
            if current_thread_id:
                self.collaboration.update_thread(current_thread_id, character_id=character_id)
            return {"thread_id": current_thread_id, "inherited": True}

        existing = [
            thread
            for thread in self.collaboration.list_threads(project_id)
            if str(thread.get("character_id") or "") == character_id
        ]
        if existing:
            # Most recently used, so returning to a character resumes where
            # that character left off rather than starting over every time.
            target = max(existing, key=lambda thread: float(thread.get("updated_at") or 0))
            self.collaboration.activate_thread(str(target["id"]))
            return {"thread_id": str(target["id"]), "inherited": False, "created": False}

        # create_thread activates what it creates.
        created = self.collaboration.create_thread(project_id, character_id=character_id)
        thread = created.get("thread") if isinstance(created.get("thread"), dict) else {}
        return {"thread_id": str(thread.get("id") or ""), "inherited": False, "created": True}

    def character_duplicate_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        return self._character_preview_result(
            self._character_command(
                lambda: self.app.character_packages.duplicate(
                    str(params.get("character_id") or ""),
                    str(params.get("name") or ""),
                )
            )
        )

    def character_uninstall_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        character_id = str(params.get("character_id") or "")
        fallback_id = str(params.get("fallback_id") or "")

        def uninstall() -> dict[str, Any]:
            detail = self.app.character_packages.detail(character_id).get("character") or {}
            if bool(detail.get("built_in")):
                raise CharacterPackageError("builtin_character_required", "内置角色不能卸载。")
            switched = character_id == self.app.character.id
            if switched:
                if not fallback_id or fallback_id == character_id:
                    raise CharacterPackageError("active_character_required", "卸载当前角色前需要提供备用角色。")
                self.app.character_packages.activate(fallback_id)
                self._reload_active_character()
            result = self.app.character_packages.uninstall(character_id)
            result["active_id"] = self.app.character.id
            if switched:
                result["ready"] = self._ready_payload()
            return result

        return self._character_command(uninstall)

    def _reload_active_character(self) -> None:
        self.watch_loop.stop(emit=False)
        self.app.reload_character_package()
        self.memory_service = MemoryService(self.app.memory)
        self.tts.reload()
        self.watch_commentary.character = self.app.character
        self.watch_commentary.reload()

    def _activate_thread_context(self, thread_id: str) -> dict[str, Any]:
        result = self.collaboration.activate_thread(thread_id)
        if not result.get("ok"):
            return result
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        active = result.get("active") if isinstance(result.get("active"), dict) else {}
        self._activate_thread_character(str(thread.get("character_id") or active.get("character_id") or ""))
        return {**result, "collaboration": self.collaboration.snapshot(), "ready": self._ready_payload()}

    def _activate_thread_character(self, character_id: str) -> None:
        if not character_id or character_id == self.app.character.id:
            return
        try:
            self.app.character_packages.activate(character_id)
            self._reload_active_character()
        except CharacterPackageError:
            # Historical threads remain usable if their character was removed.
            self.collaboration.update_thread(self.collaboration.context()["thread_id"], character_id=self.app.character.id)

    def character_check_updates_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        character_id = str((params or {}).get("character_id") or "")
        return self._character_command(lambda: self.app.character_packages.check_updates(character_id))

    def character_install_update_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        character_id = str(params.get("character_id") or "")
        package_url = str(params.get("package_url") or "")

        def install() -> dict[str, Any]:
            result = self.app.character_packages.install_update(character_id, package_url)
            if character_id == self.app.character.id and result.get("updated"):
                self.app.reload_character_package()
                self.memory_service = MemoryService(self.app.memory)
                self.tts.reload()
                self.watch_commentary.character = self.app.character
                self.watch_commentary.reload()
            return result

        return self._character_command(install)

    @staticmethod
    def _character_command(callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return callback()
        except CharacterPackageError as exc:
            return exc.to_payload()

    def _character_preview_result(self, result: dict[str, Any]) -> dict[str, Any]:
        character = result.get("character") if isinstance(result.get("character"), dict) else None
        if character is None:
            return result
        self._attach_character_image(character, "avatar_path", "avatar_url", "avatar_data_url")
        self._attach_character_image(character, "portrait_path", "portrait_url", "portrait_data_url")
        return result

    def _attach_character_animations(self, payload: dict[str, Any]) -> None:
        """Publish authored `.vrma` clips as asset URLs and drop their paths.

        The shell renders motion, so it needs somewhere to fetch a clip from,
        but a local filesystem path is not something a safe UI payload carries.
        """

        rows = payload.get("motion_mappings")
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = Path(str(row.pop("animation_path", "") or ""))
            if not str(path) or path == Path("."):
                continue
            url = self._character_asset_url(path)
            if url:
                row["animation_url"] = url

    def _attach_character_image(
        self,
        payload: dict[str, Any],
        path_key: str,
        url_key: str,
        data_key: str,
    ) -> None:
        """Expose installed images through the local asset server without leaking paths.

        A data URL remains as a fallback only when the read-only asset service is not
        available. This keeps ordinary list/ready RPC responses small while preserving
        preview support in tests and constrained runtimes.
        """

        path = Path(str(payload.get(path_key) or ""))
        asset_url = self._character_asset_url(path)
        payload[url_key] = asset_url
        payload[data_key] = "" if asset_url else self._image_data_url(path) if path.is_file() else ""
        payload.pop(path_key, None)

    def _active_approval_ids(self) -> list[str]:
        return sorted({*self.app.pending_steps, *self.codex_runtime.pending_approval_ids()})

    async def _broadcast_ready(self) -> None:
        ready_payload = await asyncio.to_thread(self._ready_payload)
        await self._broadcast(
            json.dumps(
                {"jsonrpc": "2.0", "method": "core.ready", "params": ready_payload},
                ensure_ascii=False,
            )
        )

    async def _event_pump(self) -> None:
        assert self.queue is not None
        while True:
            event = await self.queue.get()
            payload = event.to_dict()
            message = json.dumps({"jsonrpc": "2.0", "method": "agent.event", "params": payload}, ensure_ascii=False)
            await self._broadcast(message)
            if _event_applied_runtime_config(event):
                self._reload_runtime_after_config_change()
                await self._broadcast_ready()
            if event.type in SPEAKABLE_EVENTS:
                asyncio.create_task(self._synthesize_voice(event))

    async def _synthesize_voice(self, event: AgentEvent) -> None:
        lock = getattr(self, "_tts_speaker_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._tts_speaker_lock = lock
        async with lock:
            await self._synthesize_voice_unlocked(event)

    async def _synthesize_voice_unlocked(self, event: AgentEvent) -> None:
        generation = str(event.agent_state.get("voice_generation") or "")
        thread_id = str(event.thread_id or "")
        generations = self.app.voice_generations
        if not generations.is_current(generation, thread_id):
            # Already superseded before synthesis even started.
            generations.drop(generation, thread_id, "superseded")
            return
        if bool(getattr(self.tts, "supports_streaming", False)):
            await self._stream_voice(event, generation, thread_id)
            return
        audio = await asyncio.to_thread(
            self.tts.synthesize,
            event.voice_line.text,
            event.voice_line.sprite,
            event.voice_line.emotion,
            event.voice_line.delivery,
        )
        if not audio:
            return
        if not audio.get("voice_audio_path") and not audio.get("voice_audio_error"):
            return
        if not generations.is_current(generation, thread_id):
            # Synthesis outlived its turn: the user has moved on, so this audio
            # is never played. Only the fact is recorded, never the text.
            generations.drop(generation, thread_id, "superseded")
            return
        payload = {
            "voice_generation": generation,
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

    async def _stream_voice(self, event: AgentEvent, generation: str, thread_id: str) -> None:
        """Forward MiMo PCM chunks immediately instead of waiting for a WAV."""

        stream = self.tts.synthesize_stream(
            event.voice_line.text,
            event.voice_line.emotion,
            event.voice_line.delivery,
        )
        try:
            while True:
                audio = await asyncio.to_thread(_next_voice_audio_payload, stream)
                if audio is None:
                    return
                if not self.app.voice_generations.is_current(generation, thread_id):
                    self.app.voice_generations.drop(generation, thread_id, "superseded")
                    return
                payload = {
                    "voice_generation": generation,
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
                message = json.dumps(
                    {"jsonrpc": "2.0", "method": "agent.voice_audio", "params": payload},
                    ensure_ascii=False,
                )
                await self._broadcast(message)
                if audio.get("voice_audio_final"):
                    return
        finally:
            try:
                stream.close()
            except (RuntimeError, ValueError):
                # A shutdown can cancel this coroutine while the worker is
                # still inside `next()`. The response then closes with that
                # worker; never turn shutdown into a second error.
                pass

    async def _synthesize_realtime_text(self, owner_id: str, event: dict[str, Any]) -> None:
        """Speak safe Qwen text through the selected local GPT-SoVITS voice.

        Realtime never falls back to provider audio, a system voice, or a cloud
        TTS voice. The Shell gets captions plus an explicit muted state when
        the local character voice is unavailable.
        """

        session_id = str(event.get("session_id") or "")
        epoch = max(0, int(event.get("epoch") or 0))
        self._realtime_epochs[session_id] = max(self._realtime_epochs.get(session_id, 0), epoch)
        line = safe_voice_line(str(event.get("text") or ""), fallback="")
        status = self.tts.status_payload()
        if not line.text or str(status.get("provider") or "").strip().casefold() != "gpt-sovits" or not status.get("configured"):
            await self._send_realtime_tts_state(owner_id, session_id, epoch, "muted", "realtime_local_tts_unavailable")
            return
        lock = getattr(self, "_tts_speaker_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._tts_speaker_lock = lock
        async with lock:
            if self._realtime_epochs.get(session_id, -1) != epoch:
                return
            stream = self.tts.synthesize_stream(line.text, line.emotion, line.delivery)
            try:
                while True:
                    audio = await asyncio.to_thread(_next_voice_audio_payload, stream)
                    if audio is None:
                        return
                    if self._realtime_epochs.get(session_id, -1) != epoch:
                        return
                    if audio.get("voice_audio_error"):
                        await self._send_realtime_tts_state(owner_id, session_id, epoch, "muted", "realtime_local_tts_failed")
                        return
                    payload = {
                        "realtime_session_id": session_id,
                        "realtime_epoch": epoch,
                        "voice_text": line.text,
                        "voice_emotion": line.emotion,
                        "voice_sprite": line.sprite,
                        **audio,
                    }
                    # Refuse accidental provider fallback even if the TTS
                    # implementation changes under this call in the future.
                    if payload.get("voice_audio_source") not in {"local", None}:
                        await self._send_realtime_tts_state(owner_id, session_id, epoch, "muted", "realtime_local_tts_unavailable")
                        return
                    await self._send_to_owner(
                        owner_id,
                        json.dumps({"jsonrpc": "2.0", "method": "agent.voice_audio", "params": payload}, ensure_ascii=False),
                    )
                    if audio.get("voice_audio_final"):
                        return
            finally:
                try:
                    stream.close()
                except (RuntimeError, ValueError):
                    pass

    async def _send_realtime_tts_state(
        self,
        owner_id: str,
        session_id: str,
        epoch: int,
        state: str,
        error: str,
    ) -> None:
        payload = {
            "session_id": session_id,
            "type": "tts_state",
            "state": state if state == "muted" else "muted",
            "error": error if error in {"realtime_local_tts_unavailable", "realtime_local_tts_failed"} else "realtime_local_tts_failed",
            "epoch": epoch,
        }
        await self._send_to_owner(
            owner_id,
            json.dumps({"jsonrpc": "2.0", "method": "voice.realtime.event", "params": payload}, ensure_ascii=False),
        )

    def transcribe_audio(self, audio_base64: str, mime_type: str = "") -> dict[str, Any]:
        """Decode and recognize one bounded clip, with safe stage timings.

        Timings are coarse integer milliseconds and contain no transcript,
        endpoint, model, path, key, or provider error detail. They are useful
        for deciding whether the delay is local preparation, transport/ASR, or
        the separate LLM turn without expanding the diagnostic data surface.
        """

        started = time.perf_counter()
        if _encoded_audio_exceeds_limit(audio_base64, self.asr_state.max_bytes):
            return self._asr_result_error("audio_too_large", started)
        decode_started = time.perf_counter()
        audio = _decode_audio_base64(audio_base64)
        decode_ms = _elapsed_ms(decode_started)
        if audio_base64 and not audio:
            return self._asr_result_error("audio_decode_failed", started, decode_ms=decode_ms)
        if not self.asr_state.configured:
            return self._asr_result_error(self.asr_state.error or "asr_unconfigured", started, decode_ms=decode_ms)
        if len(audio) > self.asr_state.max_bytes:
            return self._asr_result_error("audio_too_large", started, decode_ms=decode_ms)
        provider_started = time.perf_counter()
        result = self.asr.transcribe(audio, mime_type)
        provider_ms = _elapsed_ms(provider_started)
        if not result.ok:
            error_code = _safe_asr_error_code(result.error)
            return self._asr_result_error(
                error_code,
                started,
                decode_ms=decode_ms,
                provider_ms=provider_ms,
            )
        payload: dict[str, Any] = {
            "ok": result.ok,
            "transcript": result.transcript,
            "confidence": result.confidence,
            "provider": result.provider,
            "submitted": False,
            "latency": {
                "decode_ms": decode_ms,
                "provider_ms": provider_ms,
                "total_ms": _elapsed_ms(started),
            },
        }
        return payload

    def transcribe_and_submit(self, audio_base64: str, mime_type: str = "") -> dict[str, Any]:
        """Synchronous compatibility path used by direct Core callers/tests."""

        payload = self.transcribe_audio(audio_base64, mime_type)
        if not payload.get("ok"):
            error = str(payload.get("error") or "asr_failed")
            failure = self._asr_error(error, str(payload.get("message") or _friendly_asr_message(error)))
            failure["latency"] = payload.get("latency", {})
            return failure
        submitted = self.submit_user_text(str(payload.get("transcript") or ""))
        payload["submitted"] = True
        payload["sequence"] = submitted.get("sequence")
        payload["events"] = submitted.get("events", [])
        if "watch_loop" in submitted:
            payload["watch_loop"] = submitted["watch_loop"]
        return payload

    def _asr_result_error(
        self,
        error: str,
        started: float,
        *,
        decode_ms: int = 0,
        provider_ms: int = 0,
    ) -> dict[str, Any]:
        error_code = _safe_asr_error_code(error)
        return {
            "ok": False,
            "submitted": False,
            "transcript": "",
            "error": error_code,
            "message": _friendly_asr_message(error_code),
            "latency": {
                "decode_ms": max(0, int(decode_ms)),
                "provider_ms": max(0, int(provider_ms)),
                "total_ms": _elapsed_ms(started),
            },
        }

    def _set_voice_generation(self, thread_id: str, generation_id: str) -> None:
        key = thread_id or "__active__"
        with self._voice_generation_lock:
            # Dicts preserve insertion order. Refreshing an existing thread
            # makes it the newest marker; evicting an older marker is fail-safe
            # because any still-running ASR for it will become stale.
            self._voice_generations.pop(key, None)
            while len(self._voice_generations) >= MAX_VOICE_INPUT_GENERATIONS:
                self._voice_generations.pop(next(iter(self._voice_generations)))
            self._voice_generations[key] = generation_id

    def _voice_generation_is_current(self, thread_id: str, generation_id: str) -> bool:
        with self._voice_generation_lock:
            return self._voice_generations.get(thread_id or "__active__") == generation_id

    def _voice_request_is_current(self, thread_id: str, generation_id: str) -> bool:
        if not self._voice_generation_is_current(thread_id, generation_id):
            return False
        if thread_id and hasattr(self, "collaboration"):
            active_thread = str(self.collaboration.context().get("thread_id") or "")
            return active_thread == thread_id
        return True

    def _submit_voice_transcript(self, transcript: str, thread_id: str, generation_id: str) -> None:
        if not self._voice_request_is_current(thread_id, generation_id):
            return
        self.submit_user_text(transcript)

    def submit_user_text(self, text: str) -> dict[str, Any]:
        context = self.collaboration.context()
        thread = self.collaboration.get_thread(context["thread_id"])
        if thread and thread.title == "新对话":
            self.collaboration.update_thread(thread.id, title=text[:36])
        if _looks_like_computer_goal(text) and not context.get("session_id"):
            driver = self.capability_orchestrator.driver_inventory("auto").selected
            started = self.collaboration.start_session("computer_use", text, "collaborate", driver=driver)
            session = started.get("session") if isinstance(started.get("session"), dict) else {}
            self.app.set_computer_driver(driver, str(session.get("id") or ""))
        if _looks_like_watch_loop_stop(text):
            return self.watch_loop_stop_command()
        if self.codex_runtime.should_handle(text) and not self.app.should_handle_locally_before_agent_cli(text):
            return self.codex_runtime.run_user_text(text)
        if self._agent_cli_takeover_enabled(text):
            status = self.agent_cli_takeover
            sequence, events = self._run_serial(
                "user.message.agent_cli",
                lambda: self.app.handle_agent_cli_text(
                    text,
                    cli_id=str(status.get("selected") or "codex"),
                    model=str(status.get("model") or "默认"),
                    reasoning=str(status.get("reasoning") or "默认"),
                ),
            )
        else:
            sequence, events = self._run_serial("user.message", lambda: self.app.handle_user_text(text))
        payload: dict[str, Any] = {"ok": True, "submitted": True, "sequence": sequence, "events": [event.to_dict() for event in events]}
        if _looks_like_watch_loop_start(text):
            watch_result = self.watch_loop_start_command({"query": text})
            payload["watch_loop"] = watch_result.get("watch_loop") or {}
            payload["capability_session"] = watch_result.get("capability_session") or {}
        return payload

    def runtime_status_command(self) -> dict[str, Any]:
        return {
            "ok": True,
            "runtime": self.codex_runtime.status_payload(),
            "asr": self._asr_payload(),
            "realtime_voice": self._realtime_voice_payload(),
            "tts": self.tts.status_payload(),
            "joi_mcp": dict(self._joi_mcp_status),
            "byok": self.byok.status(probe_secret=False),
        }

    def byok_connect_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.byok.connect(params if isinstance(params, dict) else {})

    def runtime_configure_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime = self.codex_runtime.configure(params if isinstance(params, dict) else {})
        return {"ok": True, "runtime": runtime}

    def runtime_start_command(self) -> dict[str, Any]:
        return {"ok": True, "runtime": self.codex_runtime.start()}

    def runtime_stop_command(self) -> dict[str, Any]:
        return {"ok": True, "runtime": self.codex_runtime.stop()}

    def runtime_approval_resolve_command(self, approval_id: str, approved: bool) -> dict[str, Any]:
        if not approval_id:
            return {"ok": False, "error": "missing_approval_id"}
        result = self.codex_runtime.resolve_approval(approval_id, approved)
        if result is None:
            return {"ok": False, "error": "unknown_runtime_approval"}
        return {"ok": True, "submitted": True, **result}

    def joi_mcp_status_command(self) -> dict[str, Any]:
        codex = _codex_executable()
        if not codex:
            self.codex_runtime.mark_mcp_connected(False)
            self._joi_mcp_status = {"connected": False, "available": False, "status": "codex_not_found"}
            return {"ok": True, "joi_mcp": dict(self._joi_mcp_status)}
        try:
            probe = subprocess.run([codex, "mcp", "list"], cwd=str(self.workspace), capture_output=True, text=True, timeout=8)
        except Exception:
            self.codex_runtime.mark_mcp_connected(False)
            self._joi_mcp_status = {"connected": False, "available": True, "status": "probe_failed"}
            return {"ok": True, "joi_mcp": dict(self._joi_mcp_status)}
        output = f"{probe.stdout}\n{probe.stderr}"
        connected = probe.returncode == 0 and any(line.strip().startswith("joi") or line.strip().split(" ", 1)[0] == "joi" for line in output.splitlines())
        self.codex_runtime.mark_mcp_connected(connected)
        self._joi_mcp_status = {
            "connected": connected,
            "available": True,
            "status": "connected" if connected else "not_connected",
        }
        return {
            "ok": True,
            "joi_mcp": dict(self._joi_mcp_status),
        }

    def joi_mcp_install_codex_command(self) -> dict[str, Any]:
        codex = _codex_executable()
        if not codex:
            return {"ok": False, "error": "codex_not_found", "joi_mcp": {"connected": False, "available": False}}
        subprocess.run([codex, "mcp", "remove", "joi"], cwd=str(self.workspace), capture_output=True, text=True, timeout=8)
        command = [
            codex,
            "mcp",
            "add",
            "--env",
            f"PYTHONPATH={self.workspace}",
            "joi",
            "--",
            sys.executable,
            "-m",
            "agent_companion.core.joi_mcp_server",
            "--workspace",
            str(self.workspace),
        ]
        try:
            result = subprocess.run(command, cwd=str(self.workspace), capture_output=True, text=True, timeout=20)
        except Exception:
            return {"ok": False, "error": "install_failed", "joi_mcp": {"connected": False, "available": True}}
        ok = result.returncode == 0
        self.codex_runtime.mark_mcp_connected(ok)
        self._joi_mcp_status = {"connected": ok, "available": True, "status": "connected" if ok else "install_failed"}
        return {
            "ok": ok,
            "error": "" if ok else "codex_mcp_add_failed",
            "joi_mcp": dict(self._joi_mcp_status),
        }

    def skill_run_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        installation_id = str(params.get("installation_id") or "")
        if installation_id:
            session_id = str(params.get("session_id") or self.collaboration.context().get("session_id") or "")
            session = self.collaboration.session_payload(session_id, include_receipts=False) if session_id else {}
            profile = str(params.get("permission_profile") or session.get("permission_profile") or "observe")
            try:
                return self.agent_skills.run(
                    installation_id,
                    script=str(params.get("script") or ""),
                    arguments=params.get("arguments") if isinstance(params.get("arguments"), list) else [],
                    permission_profile=profile,
                    approved=bool(params.get("approved")),
                    project_root=str(params.get("project_root") or self.workspace),
                    timeout_seconds=optional_int(params.get("timeout_seconds")) or 120,
                )
            except AgentSkillError as exc:
                return {"ok": False, "error": exc.code, "message": exc.message}
        tool = str(params.get("tool") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        sequence, events = self._run_serial(
            "skill.run",
            lambda: self.app.handle_skill_run(tool, arguments, user_text=f"Joi skill: {tool}"),
        )
        return {"ok": True, "submitted": True, "sequence": sequence, "events": [event.to_dict() for event in events]}

    def agent_skill_list_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        context = self.collaboration.context()
        return self.agent_skills.list(
            project_id=str(params.get("project_id") or context.get("project_id") or ""),
            character_id=str(params.get("character_id") or context.get("character_id") or self.app.character.id),
            include_disabled=bool(params.get("include_disabled", True)),
        )

    def agent_skill_inspect_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self.agent_skills.inspect(str((params or {}).get("source") or ""))
        except AgentSkillError as exc:
            return {"ok": False, "error": exc.code, "message": exc.message}

    def agent_skill_install_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        scope = str(params.get("scope") or "global")
        try:
            return self.agent_skills.install(
                str(params.get("source") or ""),
                scope=scope,
                scope_id=self._agent_skill_scope_id(scope, str(params.get("scope_id") or "")),
                expected_digest=str(params.get("expected_digest") or ""),
            )
        except AgentSkillError as exc:
            return {"ok": False, "error": exc.code, "message": exc.message}

    def agent_skill_update_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        try:
            return self.agent_skills.update(str(params.get("installation_id") or ""), expected_digest=str(params.get("expected_digest") or ""))
        except AgentSkillError as exc:
            return {"ok": False, "error": exc.code, "message": exc.message}

    def agent_skill_uninstall_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        try:
            return self.agent_skills.uninstall(str(params.get("installation_id") or ""), confirmed=bool(params.get("confirmed")))
        except AgentSkillError as exc:
            return {"ok": False, "error": exc.code, "message": exc.message}

    def agent_skill_validate_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.agent_skills.validate(str((params or {}).get("installation_id") or ""))

    def agent_skill_enable_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        return self.agent_skills.set_enabled(str(params.get("installation_id") or ""), bool(params.get("enabled", True)))

    def agent_skill_draft_list_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        context = self.collaboration.context()
        project_id = str(params.get("project_id") or context.get("project_id") or DEFAULT_PROJECT_ID)
        thread_id = str(params.get("thread_id") or "")
        return {"ok": True, "drafts": self.collaboration.list_skill_drafts(project_id, thread_id)}

    def agent_skill_draft_create_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        context = self.collaboration.context()
        draft = self.collaboration.create_skill_draft(
            str(params.get("project_id") or context.get("project_id") or DEFAULT_PROJECT_ID),
            str(params.get("thread_id") or context.get("thread_id") or DEFAULT_THREAD_ID),
            str(params.get("name") or "可复用流程"),
            params.get("draft") if isinstance(params.get("draft"), dict) else {},
        )
        return {"ok": True, "draft": draft}

    def agent_skill_draft_approve_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        scope = str(params.get("scope") or "project")
        try:
            return self.agent_skills.install_draft(
                str(params.get("draft_id") or ""),
                scope=scope,
                scope_id=self._agent_skill_scope_id(scope, str(params.get("scope_id") or "")),
            )
        except AgentSkillError as exc:
            return {"ok": False, "error": exc.code, "message": exc.message}

    def agent_skill_draft_reject_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.collaboration.update_skill_draft(str((params or {}).get("draft_id") or ""), "rejected")

    def _agent_skill_scope_id(self, scope: str, requested: str) -> str:
        if scope == "project":
            return requested or str(self.collaboration.context().get("project_id") or DEFAULT_PROJECT_ID)
        if scope == "character":
            return requested or str(self.collaboration.context().get("character_id") or self.app.character.id)
        return ""

    def watch_loop_start_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        self.watch_commentary.reset()
        options = self._watch_loop_options_from_params(params)
        self.scene_session.reset(mode=options.mode, spoiler_level=options.spoiler_level, min_comment_interval_seconds=options.commentary_interval_seconds)
        capability = self.collaboration.start_session("scene_watch", options.query, "observe", driver="scene")
        snapshot = self.watch_loop.start(options)
        self._emit_watch_loop_state("实时陪看已启动。", snapshot.to_agent_state(), "success")
        return {"ok": True, "watch_loop": snapshot.to_agent_state(), "capability_session": capability.get("session") or {}}

    def watch_loop_stop_command(self) -> dict[str, Any]:
        snapshot = self.watch_loop.stop()
        context = self.collaboration.context()
        session = self.collaboration.session_payload(str(context.get("session_id") or ""), include_receipts=False)
        if session.get("capability") == "scene_watch":
            self.collaboration.transition_session(str(session.get("id") or ""), "completed")
        self._emit_watch_loop_state("实时陪看已停止。", snapshot.to_agent_state(), "info")
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
            "mode": current.mode or "quiet",
            "spoiler_level": current.spoiler_level or "none",
        }
        if isinstance(params, dict):
            merged.update(params)
        snapshot = self.watch_loop.configure(self._watch_loop_options_from_params(merged))
        self.scene_session.configure(
            mode=snapshot.mode,
            spoiler_level=snapshot.spoiler_level,
            min_comment_interval_seconds=snapshot.commentary_interval_seconds,
        )
        if not snapshot.proactive_enabled:
            self.watch_commentary.reset()
        self._emit_watch_loop_state("实时陪看设置已更新。", snapshot.to_agent_state(), "info")
        return {"ok": True, "watch_loop": snapshot.to_agent_state()}

    def watch_loop_refresh_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.watch_loop.snapshot()
        if not current.active:
            return {"ok": False, "error": "watch_loop_inactive", "watch_loop": current.to_agent_state()}
        force_visual = bool_or(params.get("force_visual_summary"), False) if isinstance(params, dict) else False
        snapshot = self.watch_loop.refresh(force_visual_summary=force_visual)
        self._emit_watch_loop_state("画面理解已更新。", snapshot.to_agent_state(), "success")
        return {"ok": True, "watch_loop": snapshot.to_agent_state()}

    def watch_loop_status_command(self) -> dict[str, Any]:
        return {"ok": True, "watch_loop": self.watch_loop.snapshot().to_agent_state()}

    def background_status_command(self) -> dict[str, Any]:
        return self.background_service.status()

    def background_configure_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.background_service.configure(params)

    def background_clear_command(self) -> dict[str, Any]:
        return self.background_service.clear()

    def memory_status_command(self) -> dict[str, Any]:
        return self.memory_service.status()

    def skill_manifest_command(self) -> dict[str, Any]:
        tts_status = self.tts.status_payload()
        context = self.collaboration.context()
        return {
            "ok": True,
            "skills": build_native_skill_manifest(
                self.workspace,
                asr_state=self.asr_state,
                tts_status=tts_status,
                memory_status=self.app.memory.status(),
                skill_settings=self.app.skill_settings_payload(),
            ),
            "installations": self.agent_skills.list(
                project_id=str(context.get("project_id") or ""),
                character_id=str(context.get("character_id") or self.app.character.id),
            )["skills"],
        }

    def agent_cli_list_command(self) -> dict[str, Any]:
        return scan_agent_clis()

    def agent_cli_test_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        cli_id = str(params.get("id") or "") if isinstance(params, dict) else ""
        return test_agent_cli(cli_id)

    def agent_cli_configure_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        current = dict(self.agent_cli_takeover)
        mode = _safe_agent_cli_mode(params.get("mode") or current.get("mode"))
        selected = _safe_agent_cli_token(params.get("selected") or params.get("cli_id") or current.get("selected") or "codex")
        enabled = bool_or(params.get("enabled"), bool(current.get("enabled")))
        if mode != "local_cli":
            enabled = False
        self.agent_cli_takeover = {
            "enabled": bool(enabled),
            "mode": mode,
            "selected": selected or "codex",
            "model": _safe_agent_cli_label(params.get("model") or current.get("model") or "默认"),
            "reasoning": _safe_agent_cli_label(params.get("reasoning") or current.get("reasoning") or "默认"),
        }
        runtime = self.codex_runtime.configure(self.agent_cli_takeover)
        return {"ok": True, "agent_cli": self._agent_cli_status_payload(), "codex_runtime": runtime}

    def agent_cli_status_command(self) -> dict[str, Any]:
        return {"ok": True, "agent_cli": self._agent_cli_status_payload()}

    def audit_recent_command(self, limit: int = 50) -> dict[str, Any]:
        return {"ok": True, "audit": self.app.audit_store.recent(limit)}

    def memory_recall_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.memory_service.recall(params)

    def memory_list_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.memory_service.list(params)

    def memory_pending_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.memory_service.pending(params)

    def memory_browse_vault_command(self) -> dict[str, Any]:
        return self.memory_service.browse_vault()

    def memory_save_candidate_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.memory_service.save_candidate(params)

    def memory_reject_candidate_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.memory_service.reject_candidate(params)

    def memory_set_enabled_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.memory_service.set_enabled(params)

    def memory_delete_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.memory_service.delete(params)

    def memory_update_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.memory_service.update(params)

    def memory_clear_command(self) -> dict[str, Any]:
        return self.memory_service.clear()

    def resolve_approval_command(self, approval_id: str, approved: bool) -> dict[str, Any]:
        if self.codex_runtime.has_pending_approval(approval_id):
            return self.runtime_approval_resolve_command(approval_id, approved)
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
        return self.artifacts.read_image(artifact)

    def _watch_loop_options_from_params(self, params: dict[str, Any]) -> WatchLoopOptions:
        query = str(params.get("query") or "陪我看当前视频").strip()
        sample_interval_ms = optional_int(params.get("sample_interval_ms"))
        vision_interval_ticks = optional_int(params.get("vision_interval_ticks"))
        return WatchLoopOptions(
            query=query or "陪我看当前视频",
            interval_seconds=float_or(params.get("interval_seconds"), 6.0),
            sample_count=optional_int(params.get("sample_count")) or 3,
            sample_interval_ms=sample_interval_ms if sample_interval_ms is not None else 700,
            transcript_source=str(params.get("transcript_source") or "system_audio"),
            transcribe=bool(params.get("transcribe", True)),
            proactive_enabled=bool_or(params.get("proactive_enabled"), True),
            commentary_interval_seconds=float_or(params.get("commentary_interval_seconds"), 30.0),
            vision_interval_ticks=vision_interval_ticks if vision_interval_ticks is not None else 5,
            mode=str(params.get("mode") or "quiet"),
            spoiler_level=str(params.get("spoiler_level") or "none"),
        )

    def _watch_loop_tick(self, options: WatchLoopOptions) -> WatchLoopTick:
        next_iteration = self.watch_loop.snapshot().iterations + 1
        run_vision_summary = _watch_loop_should_summarize(options, next_iteration)
        # The watch loop ticks on its own timer, so it has to queue behind the
        # conversation's own work instead of observing mid-action.
        _, result = self._run_serial(
            "watch.loop.tick",
            lambda: self.app.refresh_watch_context(
                options.query,
                sample_count=options.sample_count,
                sample_interval_ms=options.sample_interval_ms,
                transcript_source=options.transcript_source,
                transcribe=options.transcribe,
                skip_summary=not run_vision_summary,
            ),
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
        scene = self.scene_session.observe(
            visual_summary,
            [str(text) for text in rolling.get("recent_text", []) if str(text).strip()],
            transcript_source=str(transcript.get("source") or options.transcript_source),
            proactive_enabled=options.proactive_enabled,
        )
        comment = self.watch_commentary.maybe_comment(
            rolling,
            min_interval_seconds=options.commentary_interval_seconds,
            mode=options.mode,
            spoiler_level=options.spoiler_level,
            visual_summary=visual_summary,
        ) if scene.should_comment else None
        tick = WatchLoopTick(
            ok=result.ok,
            summary=result.display_card.summary,
            transcript_text=transcript_text,
            transcript_source=str(transcript.get("source") or options.transcript_source),
            transcript_status=str(transcript.get("status") or ""),
            rolling_summary=str(rolling.get("summary") or ""),
            rolling_transcript=[str(text) for text in rolling.get("recent_text", []) if str(text).strip()],
            transcript_window_seconds=optional_int(rolling.get("window_seconds")) or 0,
            source_health=source_health,
            proactive_reply=comment.reply if comment else "",
            proactive_voice_text=comment.voice_text if comment else "",
            proactive_emotion=comment.emotion if comment else "neutral",
            proactive_sprite=comment.sprite if comment else "1",
            proactive_reason=comment.reason if comment else "",
            visual_summary=visual_summary,
            visual_status=visual_status,
            error=error,
            scene_observation=scene.payload(),
        )
        self.app.background_context.record_summary(
            tick.rolling_summary or tick.visual_summary or tick.summary,
            source="watch_loop",
            visual_status=tick.visual_status,
            transcript_source=tick.transcript_source,
        )
        return tick

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

    def _emit_watch_loop_state(self, summary: str, watch_loop: dict[str, Any], status: str = "info") -> None:
        self.app.bus.emit(
            AgentEvent(
                EventType.SKILL_COMPLETED,
                f"watch-{uuid.uuid4().hex[:8]}",
                DisplayCard("Joi", summary, status=status),
                safe_voice_line("", fallback=""),
                {
                    "tool": "joi.watch",
                    "runtime_event": "skill_completed",
                    "skill_id": "joi.watch",
                    "watch_loop": watch_loop,
                },
            )
        )

    def _asr_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.asr_state.enabled,
            "configured": self.asr_state.configured,
            "provider": self.asr_state.provider,
            "max_seconds": self.asr_state.max_seconds,
            "max_bytes": self.asr_state.max_bytes,
            "timeout_seconds": self.asr_state.timeout_seconds,
            "error": self.asr_state.error,
        }

    def _realtime_voice_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.realtime_voice_state.enabled,
            "configured": self.realtime_voice_state.configured,
            "provider": _safe_realtime_metadata(self.realtime_voice_state.provider),
            "model": _safe_realtime_metadata(self.realtime_voice_state.model),
            "output": "local_tts",
            "timeout_seconds": min(120, max(1, int(self.realtime_voice_state.timeout_seconds or 15))),
            "error": _safe_realtime_error(self.realtime_voice_state.error),
            "modes": ["conversation", "minecraft"],
        }

    def _language_payload(self) -> dict[str, Any]:
        """What Joi shows, writes and says, in one place the shell can render.

        The voice language is reported but not settable here: it belongs to the
        character package, which is where the user picks the voice itself.
        """

        language = self.app.language_settings()
        return {
            "interface": language.interface,
            "chat": language.chat,
            "chat_choices": list(CHAT_LANGUAGE_CHOICES),
            "interface_choices": ["zh"],
            "voice": self.tts.voice_language(),
        }

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

    def _run_serial(self, label: str, callback: Callable[[], list[AgentEvent]], thread_id: str = "") -> tuple[int, list[AgentEvent]]:
        """Serialize within one conversation, not across all of them.

        ``thread_id`` defaults to whichever conversation is active, which is
        correct for user-initiated commands; background work that already knows
        its conversation should pass it explicitly rather than re-reading the
        active one.
        """
        target = thread_id or str(self.collaboration.context().get("thread_id") or "")
        return self.run_coordinator.run_serial(target, callback)

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

    async def _send_to_owner(self, owner_id: str, message: str) -> None:
        """Send ephemeral microphone/transcript/audio state to one window."""

        client = self._owner_clients.get(owner_id)
        if client is None:
            await asyncio.to_thread(self.realtime_voice.stop_owner, owner_id, "transport_lost")
            return
        try:
            await client.send(message)
        except Exception:
            await asyncio.to_thread(self.realtime_voice.stop_owner, owner_id, "transport_lost")
            self.clients.discard(client)
            self._client_owners.pop(client, None)
            self._owner_clients.pop(owner_id, None)

    def _ready_payload(self) -> dict[str, Any]:
        tts_status = self.tts.status_payload()
        memory_status = self.app.memory.status()
        payload: dict[str, Any] = {
            "product": JOI_CORE_PRODUCT,
            "protocol_version": JOI_CORE_PROTOCOL_VERSION,
            "instance_id": self.instance_id,
            "health": {
                "livez": f"http://{self.host}:{self.asset_port}/livez",
                "readyz": f"http://{self.host}:{self.asset_port}/readyz",
            },
            "workspace_label": self.workspace.name,
            "workspace_bound": True,
            "event_cursor": self.app.bus.latest_sequence,
            "active_approval_ids": self._active_approval_ids(),
            "asr": self._asr_payload(),
            "realtime_voice": self._realtime_voice_payload(),
            "tts": tts_status,
            "language": self._language_payload(),
            "runtime": build_runtime_status(
                self.workspace,
                self.asr_state,
                tts_status,
                self.realtime_voice_state,
            ),
            "codex_runtime": self.codex_runtime.status_payload(),
            "joi_mcp": dict(self._joi_mcp_status),
            "watch_loop": self.watch_loop.snapshot().to_agent_state(),
            "memory": memory_status,
            "audit": self.app.audit_store.status(),
            "background": self.app.background_context.status(),
            "agent_cli": self._agent_cli_status_payload(),
            "byok": self.byok.status(probe_secret=False),
            "collaboration": self.collaboration.snapshot(),
            "agent_skills": self.agent_skills.list(
                project_id=str(self.collaboration.context().get("project_id") or ""),
                character_id=str(self.collaboration.context().get("character_id") or self.app.character.id),
            )["skills"],
            "game_adapters": self.game_adapters.list()["adapters"],
            "skills": build_native_skill_manifest(
                self.workspace,
                asr_state=self.asr_state,
                tts_status=tts_status,
                memory_status=memory_status,
                skill_settings=self.app.skill_settings_payload(),
            ),
            "character": {"id": self.app.character.id, "name": self.app.character.name, "sprites": []},
        }
        try:
            character_payload = self.app.character_packages.active_runtime_payload()
            character_payload["model_url"] = self._character_asset_url(Path(str(character_payload.get("model_path") or "")))
            self._attach_character_animations(character_payload)
            self._attach_character_image(character_payload, "avatar_path", "avatar_url", "avatar_data_url")
            self._attach_character_image(character_payload, "portrait_path", "portrait_url", "portrait_data_url")
            self._attach_character_image(character_payload, "background_path", "background_url", "background_data_url")
            character_payload["sprites"] = [
                {
                    "id": str(sprite.get("id") or "1"),
                    "label": str(sprite.get("label") or "default"),
                    "image_data_url": self._image_data_url(Path(str(sprite.get("image_path") or ""))),
                }
                for sprite in character_payload.get("sprites") or []
                if Path(str(sprite.get("image_path") or "")).is_file()
            ]
            character_payload.pop("model_path", None)
            payload["character"] = character_payload
            public_characters: list[dict[str, Any]] = []
            for row in self.app.character_packages.list():
                self._attach_character_image(row, "avatar_path", "avatar_url", "avatar_data_url")
                self._attach_character_image(row, "portrait_path", "portrait_url", "portrait_data_url")
                public_characters.append(row)
            payload["characters"] = public_characters
        except Exception:
            pass
        config = load_workspace_config(self.workspace)
        if config is None:
            return payload
        try:
            payload["runtime_settings"] = _safe_runtime_settings(config)
        except Exception:
            return payload
        return payload

    def _agent_cli_takeover_enabled(self, text: str) -> bool:
        if not bool(self.agent_cli_takeover.get("enabled")):
            return False
        if str(self.agent_cli_takeover.get("mode") or "") != "local_cli":
            return False
        return not self.app.should_handle_locally_before_agent_cli(text)

    def _agent_cli_status_payload(self) -> dict[str, Any]:
        runtime = self.codex_runtime.status_payload()
        return {
            "safe_for_display": True,
            "enabled": bool(runtime.get("enabled")),
            "mode": _safe_agent_cli_mode(runtime.get("mode")),
            "selected": _safe_agent_cli_token(runtime.get("selected") or "codex"),
            "model": _safe_agent_cli_label(runtime.get("model") or "默认"),
            "reasoning": _safe_agent_cli_label(runtime.get("reasoning") or "默认"),
            "status": str(runtime.get("status") or "ready"),
        }

    def _reload_runtime_after_config_change(self) -> None:
        self.asr, self.asr_state = build_asr_provider(self.workspace)
        self.realtime_voice.shutdown()
        self.realtime_voice, self.realtime_voice_state = build_realtime_voice_coordinator(
            self.workspace,
            execute_action=self._execute_realtime_minecraft_action,
            cancel_action=self._cancel_realtime_minecraft_action,
            control_action=self._control_realtime_minecraft_action,
            validate_binding=self._realtime_minecraft_binding_ready,
            voice_locale=self.tts.voice_language,
            chat_locale=self.app.chat_language,
            persona=self._realtime_persona_prompt,
            world_memory=self.minecraft.world_memory,
            transcript_sink=self._persist_realtime_transcripts,
        )
        self.tts.reload()
        self.watch_commentary.reload()
        self.app.reload_runtime_policy()

    def _start_character_asset_server(self) -> None:
        if self._asset_server is not None:
            return
        handler = functools.partial(
            _CharacterAssetRequestHandler,
            directory=str(self.app.character_packages.packages_dir),
            health_provider=self._health_payload,
            session_token=self.session_token,
        )
        try:
            server = http.server.ThreadingHTTPServer((self.host, self.asset_port), handler)
        except OSError:
            server = http.server.ThreadingHTTPServer((self.host, 0), handler)
        self.asset_port = int(server.server_address[1])
        self._asset_server = server
        self._asset_thread = threading.Thread(target=server.serve_forever, name="joi-character-assets", daemon=True)
        self._asset_thread.start()

    def _health_payload(self, ready: bool = True) -> dict[str, Any]:
        return {
            "ok": True,
            "ready": bool(ready),
            "product": JOI_CORE_PRODUCT,
            "protocol_version": JOI_CORE_PROTOCOL_VERSION,
            "instance_id": self.instance_id,
            "pid": os.getpid(),
            "parent_pid": self.parent_pid,
        }

    def _write_ready_file(self) -> None:
        path = self.ready_file
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        **self._health_payload(ready=True),
                        "host": self.host,
                        "port": self.port,
                        "asset_port": self.asset_port,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            return

    def _remove_ready_file(self) -> None:
        path = self.ready_file
        if path is None or not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if str(payload.get("instance_id") or "") == self.instance_id:
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            return

    def _stop_character_asset_server(self) -> None:
        server = self._asset_server
        self._asset_server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        self._asset_thread = None

    def _character_asset_url(self, path: Path) -> str:
        if not path.is_file() or not self.asset_port:
            return ""
        try:
            relative = path.resolve().relative_to(self.app.character_packages.packages_dir.resolve()).as_posix()
        except ValueError:
            return ""
        relative_url = urllib.parse.quote(relative, safe="/")
        if self.session_token:
            token = urllib.parse.quote(self.session_token, safe="")
            return f"http://{self.host}:{self.asset_port}/characters/{token}/{relative_url}"
        return f"http://{self.host}:{self.asset_port}/characters/{relative_url}"

    @staticmethod
    def _image_data_url(path: Path) -> str:
        try:
            if path.stat().st_size > 12 * 1024 * 1024:
                return ""
            data = path.read_bytes()
        except Exception:
            return ""
        mime = "image/webp" if path.suffix.lower() == ".webp" else mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> str:
        response_id = request_id if request_id is not None else f"server-{uuid.uuid4().hex[:8]}"
        return encode_result(response_id, result)

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> str:
        return encode_error(request_id, code, message)


class _CharacterAssetRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Read-only HTTP surface scoped to installed character assets."""

    def __init__(
        self,
        *args: Any,
        health_provider: Callable[[bool], dict[str, Any]],
        session_token: str = "",
        **kwargs: Any,
    ) -> None:
        self.health_provider = health_provider
        self.session_token = session_token
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self._serve_health():
            return
        if not self._prepare_character_path():
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler contract
        if self._serve_health(head_only=True):
            return
        if not self._prepare_character_path():
            self.send_error(404)
            return
        super().do_HEAD()

    def _serve_health(self, *, head_only: bool = False) -> bool:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path not in {"/livez", "/readyz"}:
            return False
        if parsed.path == "/readyz" and self.session_token:
            token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
            if token != self.session_token:
                self.send_error(401)
                return True
        payload = json.dumps(
            self.health_provider(parsed.path == "/readyz"),
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)
        return True

    def _prepare_character_path(self) -> bool:
        parsed = urllib.parse.urlsplit(self.path)
        prefix = "/characters/"
        if not parsed.path.startswith(prefix):
            return False
        encoded_relative = parsed.path[len(prefix) :]
        if self.session_token:
            encoded_token, separator, token_relative = encoded_relative.partition("/")
            path_token = urllib.parse.unquote(encoded_token)
            query_token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
            if separator and path_token == self.session_token:
                encoded_relative = token_relative
            elif query_token != self.session_token:
                return False
        relative = urllib.parse.unquote(encoded_relative)
        parts = Path(relative).parts
        if not relative or ".." in parts or any(part.startswith(".") for part in parts):
            return False
        self.path = "/" + urllib.parse.quote(relative, safe="/")
        return True

    def list_directory(self, path: str) -> None:
        self.send_error(404)
        return None

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        self.send_header("Cache-Control", "private, no-store" if self.session_token else "public, max-age=300")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def _websocket_session_token(websocket: Any) -> str:
    """Read the per-launch token across supported ``websockets`` versions."""

    request = getattr(websocket, "request", None)
    request_path = getattr(request, "path", "") if request is not None else ""
    if not request_path:
        request_path = getattr(websocket, "path", "")
    parsed = urllib.parse.urlsplit(str(request_path or ""))
    return urllib.parse.parse_qs(parsed.query).get("token", [""])[0]


def _decode_audio_base64(audio_base64: str) -> bytes:
    if not audio_base64:
        return b""
    payload = audio_base64.split(",", 1)[1] if "," in audio_base64[:80] else audio_base64
    try:
        return base64.b64decode(payload, validate=False)
    except Exception:
        return b""


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


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


def _safe_agent_cli_mode(value: object) -> str:
    text = str(value or "").strip().casefold()
    return text if text in {"local_cli", "byok"} else "byok"


def _safe_agent_cli_token(value: object) -> str:
    text = str(value or "").strip().casefold()
    return "".join(char for char in text if char.isalnum() or char in "_-")[:64] or "codex"


def _safe_agent_cli_label(value: object) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return "默认"
    if any(fragment in text.casefold() for fragment in ("sk-", "token", "secret", "api_key", "key=", "/users/", "c:\\")):
        return "默认"
    return text[:80]


def _safe_realtime_metadata(value: object) -> str:
    text = " ".join(str(value or "").split()).strip()
    lowered = text.casefold()
    if not text:
        return ""
    if any(fragment in lowered for fragment in ("sk-", "token", "secret", "api_key", "key=", "/users/", "c:\\")):
        return "redacted"
    if any(char in text for char in ("/", "\\", "{", "}", "$", "%")):
        return "redacted"
    return text[:80]


def _safe_realtime_error(value: object) -> str:
    code = str(value or "").strip().casefold()
    allowed = {
        "",
        "realtime_unconfigured",
        "realtime_config_error",
        "realtime_invalid_request",
        "realtime_invalid_response",
        "realtime_auth_failed",
        "realtime_rate_limited",
        "realtime_timeout",
        "realtime_unavailable",
    }
    return code if code in allowed else "realtime_unavailable"


def _codex_executable() -> str:
    return codex_executable()


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


def _looks_like_computer_goal(text: str) -> bool:
    value = " ".join((text or "").strip().split()).casefold()
    if not value:
        return False
    return any(
        token in value
        for token in (
            "打开",
            "点击",
            "输入",
            "帮我搜索",
            "浏览器",
            "访达",
            "finder",
            "备忘录",
            "电脑上",
            "桌面上",
            "帮我操作",
        )
    )


_SENSITIVE_ACTION_LABELS = {
    "payment": "付款或下单",
    "authentication": "登录或授权",
    "external_send": "对外发送消息",
    "deletion": "删除",
    "installation": "安装",
    "permission_expansion": "扩大权限范围",
}


_SCOPE_KIND_LABELS = {"directory": "目录", "application": "应用", "domain": "网站", "game": "游戏"}


def _scope_additions_summary(additions: dict[str, Any]) -> str:
    parts = [
        f"{_SCOPE_KIND_LABELS.get(kind, kind)}：{'、'.join(str(value) for value in values[:4])}"
        for kind, values in additions.items()
        if isinstance(values, list) and values
    ]
    return "新增 " + "；".join(parts) if parts else "权限范围没有变化。"


def _sensitive_request_signals(request: ToolRequest) -> list[str]:
    """Free-text fields that could name a sensitive action.

    The planner's ``reason`` matters most: a coordinate click onto a "立即支付"
    button looks identical to any other click in its arguments, and only the
    reason records which control was resolved.
    """
    return [request.name, *request_signals(request)]


def _request_within_bound_scope(request: ToolRequest, scope: dict[str, Any], evidence: TargetEvidence | None = None) -> bool:
    directories = [str(item) for item in scope.get("directory", []) if str(item)]
    applications = [str(item).casefold() for item in scope.get("application", []) if str(item)]
    domains = [str(item).casefold().lstrip(".") for item in scope.get("domain", []) if str(item)]
    games = [str(item).casefold() for item in scope.get("game", []) if str(item)]
    arguments = request.arguments
    requested_app = str(arguments.get("app_name") or arguments.get("app") or arguments.get("browser") or "").strip().casefold()
    if requested_app:
        return any(requested_app == app or requested_app in app or app in requested_app for app in applications)
    requested_url = str(arguments.get("url") or "").strip()
    if requested_url:
        host = (urllib.parse.urlparse(requested_url if "://" in requested_url else f"https://{requested_url}").hostname or "").casefold()
        return any(host == domain or host.endswith("." + domain) for domain in domains)
    requested_game = str(arguments.get("game") or arguments.get("game_id") or "").strip().casefold()
    if request.name.startswith("game."):
        return bool(games) and (not requested_game or any(requested_game == game or requested_game in game for game in games))
    requested_path = str(arguments.get("path") or arguments.get("directory") or arguments.get("workspace") or "").strip()
    if requested_path:
        try:
            target = Path(requested_path).expanduser().resolve()
        except OSError:
            return False
        for directory in directories:
            try:
                target.relative_to(Path(directory).expanduser().resolve())
                return True
            except (OSError, ValueError):
                continue
        return False
    # Coordinate, keyboard and DOM actions name nothing in their arguments, so
    # the only thing that can place them inside the binding is the evidence
    # gathered when the target was resolved. "The project has some binding" is
    # not evidence about *this* action, and waiting for post-action focus drift
    # to notice means the click already happened (TDD §9.1).
    if evidence is None:
        return False
    if not evaluate_evidence(evidence, evidence.identity).usable:
        return False
    observed_app = str(evidence.identity.app_id or "").strip().casefold()
    if observed_app and any(observed_app == app or observed_app in app or app in observed_app for app in applications):
        return True
    observed_domain = str(evidence.domain or "").strip().casefold().lstrip(".")
    if observed_domain and any(observed_domain == domain or observed_domain.endswith("." + domain) for domain in domains):
        return True
    observed_path = str(evidence.path or "").strip()
    if observed_path:
        try:
            resolved = Path(observed_path).expanduser().resolve()
        except OSError:
            return False
        for directory in directories:
            try:
                resolved.relative_to(Path(directory).expanduser().resolve())
                return True
            except (OSError, ValueError):
                continue
    return False


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
            "gpt_sovits_streaming_mode": max(1, min(3, int(config.tts.gpt_sovits_streaming_mode or 2))),
            # Which voice service and what to ask it for. The key is
            # deliberately absent: the panel must be able to show how the
            # voice is set up without ever being handed the credential.
            "provider": str(config.tts.provider or ""),
            "model": str(config.tts.model or ""),
            "voice": str(config.tts.voice or ""),
            "base_url": str(config.tts.base_url or ""),
            "timeout_seconds": max(1, int(config.tts.timeout_seconds or 1)),
            "optimize_text": bool(config.tts.optimize_text),
        },
        "ocr": {
            "timeout_seconds": max(1, int(config.ocr.timeout_seconds or 1)),
            "language": str(config.ocr.language or "chi_sim+eng"),
        },
        "llm": {
            "temperature": float(config.llm.temperature),
            "use_mock": bool(config.llm.use_mock),
            "provider": str(config.llm.provider or ""),
            "model": str(config.llm.model or ""),
        },
        "computer_use": {
            "post_action_settle_ms": max(0, int(config.computer_use.post_action_settle_ms or 0)),
        },
        "skills": {
            skill_id: {"enabled": bool(setting.enabled)}
            for skill_id, setting in sorted(config.skills.items())
        },
    }


def _configured_ocr_extractor(workspace: Path) -> PytesseractOcrExtractor:
    """OCR built from the workspace's own settings, defaults when unreadable."""

    try:
        ocr = load_app_config(workspace / "config.yaml").ocr
    except Exception:
        return PytesseractOcrExtractor()
    return PytesseractOcrExtractor(
        timeout_seconds=ocr.timeout_seconds,
        language=ocr.language,
        tesseract_cmd=ocr.tesseract_cmd,
        tessdata_dir=ocr.tessdata_dir,
    )


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


def _process_is_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Joi WebSocket JSON-RPC bridge.")
    parser.add_argument("--workspace", default=".", help="Workspace root")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--session-token", default=os.environ.get("JOI_CORE_SESSION_TOKEN", ""))
    parser.add_argument("--instance-id", default=os.environ.get("JOI_CORE_INSTANCE_ID", ""))
    parser.add_argument("--ready-file", default=os.environ.get("JOI_CORE_READY_FILE", ""))
    parser.add_argument("--parent-pid", type=int, default=0)
    args = parser.parse_args(argv)
    bridge = JsonRpcBridge(
        Path(args.workspace),
        args.host,
        args.port,
        session_token=args.session_token,
        instance_id=args.instance_id,
        ready_file=Path(args.ready_file) if args.ready_file else None,
        parent_pid=args.parent_pid,
    )
    asyncio.run(bridge.serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
