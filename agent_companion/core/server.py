from __future__ import annotations

import argparse
import asyncio
import base64
import functools
import http.server
import json
import mimetypes
import os
import subprocess
import sys
import threading
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
from agent_companion.core.config import load_workspace_config
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
from agent_companion.core.services import ArtifactService, BackgroundContextService, MemoryService
from agent_companion.core.skill_manifest import build_native_skill_manifest
from agent_companion.core.speech_input import AsrRuntimeState, SpeechInputProvider, build_asr_provider
from agent_companion.core.tts_bridge import TtsBridge
from agent_companion.core.voice import safe_voice_line
from agent_companion.core.watch_commentary import WatchCommentaryPlanner
from agent_companion.core.watch_loop import WatchLoopController, WatchLoopOptions, WatchLoopTick


SPEAKABLE_EVENTS = {
    EventType.APPROVAL_REQUIRED,
    EventType.RUNTIME_STARTED,
    EventType.RUNTIME_FINAL,
    EventType.RUNTIME_ERROR,
    EventType.TOOL_STARTED,
    EventType.TOOL_COMPLETED,
    EventType.TOOL_FAILED,
    EventType.TASK_COMPLETED,
    EventType.TASK_FAILED,
}

JOI_CORE_PRODUCT = "joi-core"
JOI_CORE_PROTOCOL_VERSION = 1


class JsonRpcBridge:
    def __init__(
        self,
        workspace: Path,
        host: str = "127.0.0.1",
        port: int = 8765,
        asr_provider: SpeechInputProvider | None = None,
        asr_state: AsrRuntimeState | None = None,
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
        self.clients: set[Any] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue[AgentEvent] | None = None
        # Serialization is per conversation, not global -- see RunCoordinator.
        self.run_coordinator = RunCoordinator(self.collaboration.runs)
        self.app.set_run_journal(StoreRunJournal(self.collaboration.runs, self.run_coordinator, self.collaboration.context))
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
        self.queue = asyncio.Queue()
        self.app.bus.subscribe(self._on_event)
        self._start_character_asset_server()
        try:
            async with websockets.serve(self._client_handler, self.host, self.port):
                print(f"Joi Core listening on ws://{self.host}:{self.port}")
                self._write_ready_file()
                pump = asyncio.create_task(self._event_pump())
                try:
                    if self.parent_pid > 1:
                        await self._wait_for_parent_exit()
                    else:
                        await asyncio.Future()
                finally:
                    self.watch_loop.stop(emit=False)
                    pump.cancel()
                    self.tts.shutdown()
        finally:
            self._stop_character_asset_server()
            self._remove_ready_file()

    async def _wait_for_parent_exit(self) -> None:
        while _process_is_alive(self.parent_pid):
            await asyncio.sleep(1.0)

    def _on_event(self, event: AgentEvent) -> None:
        if self.loop is None or self.queue is None:
            return
        self.loop.call_soon_threadsafe(self.queue.put_nowait, event)

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
        self.clients.add(websocket)
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
            self.clients.discard(websocket)

    async def _handle_message(self, websocket: Any, raw: str) -> None:
        request_id: Any = None
        try:
            request = parse_request(raw)
            request_id = request.request_id
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
        router.register("game.adapter.pause", self.game_adapter_pause_command, run_in_thread=True, broadcast_ready=True)
        router.register("game.adapter.resume", self.game_adapter_resume_command, run_in_thread=True, broadcast_ready=True)
        router.register("character.list", lambda _: self.character_list_command())
        router.register("character.detail", self.character_detail_command)
        router.register("character.create", self.character_create_command, run_in_thread=True, broadcast_ready=True)
        router.register("character.update", self.character_update_command, run_in_thread=True, broadcast_ready=True)
        router.register("character.inspect", self.character_inspect_command, run_in_thread=True)
        router.register("character.import", self.character_import_command, run_in_thread=True, broadcast_ready=True)
        router.register("character.export", self.character_export_command, run_in_thread=True)
        router.register("character.activate", self.character_activate_command, run_in_thread=True, broadcast_ready=True)
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
            run_in_thread=True,
        )
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

    def _rpc_voice_transcribe(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.transcribe_and_submit(
            str(params.get("audio_base64") or ""),
            str(params.get("mime_type") or ""),
        )

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

    def game_adapter_run_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        adapter_id = str(params.get("adapter_id") or "")
        mode = str(params.get("mode") or "takeover")
        goal = str(params.get("goal") or "")
        dry_run = bool(params.get("dry_run", True))
        if dry_run:
            return self.game_adapters.prepare(adapter_id, mode, goal, True)
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
        result = self.game_adapters.pause(adapter_id)
        session_id = str((params or {}).get("session_id") or self.collaboration.context().get("session_id") or "")
        if session_id:
            result["session"] = self.collaboration.transition_session(session_id, "paused").get("session") or {}
        return result

    def game_adapter_resume_command(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        adapter_id = str((params or {}).get("adapter_id") or "")
        result = self.game_adapters.resume(adapter_id)
        session_id = str((params or {}).get("session_id") or self.collaboration.context().get("session_id") or "")
        if session_id:
            result["session"] = self.collaboration.transition_session(session_id, "running").get("session") or {}
        return result

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
            self.collaboration.update_thread(self.collaboration.context()["thread_id"], character_id=character_id)
            result["memory"] = self.app.memory.status()
            result["ready"] = self._ready_payload()
            return result

        return self._character_preview_result(self._character_command(activate))

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
            "tts": tts_status,
            "runtime": build_runtime_status(self.workspace, self.asr_state, tts_status),
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
        asset_url = f"http://{self.host}:{self.asset_port}/characters/{urllib.parse.quote(relative, safe='/')}"
        if self.session_token:
            asset_url = f"{asset_url}?token={urllib.parse.quote(self.session_token, safe='')}"
        return asset_url

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
        if not parsed.path.startswith("/characters/"):
            return False
        if self.session_token:
            token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
            if token != self.session_token:
                return False
        relative = urllib.parse.unquote(parsed.path[len("/characters/") :])
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
