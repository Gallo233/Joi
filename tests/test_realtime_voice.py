from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_companion.core.config import RealtimeVoiceConfig, is_safe_qwen_realtime_url
from agent_companion.core.realtime_voice import (
    QwenRealtimeSession,
    RealtimeVoiceCoordinator,
    RealtimeVoiceRuntimeState,
    _transcript_authorizes_attack,
    build_realtime_voice_coordinator,
)
from agent_companion.core.server import JsonRpcBridge


class _FakeSocket:
    def __init__(self, incoming: list[dict[str, object]] | None = None) -> None:
        self.incoming = [json.dumps(row, ensure_ascii=False) for row in (incoming or [])]
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self._condition = threading.Condition()

    def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def recv(self, timeout: float | None = None) -> str:
        deadline = time.monotonic() + float(timeout or 1)
        with self._condition:
            while not self.incoming and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                self._condition.wait(remaining)
            if self.incoming:
                return self.incoming.pop(0)
        raise RuntimeError("closed")

    def push(self, event: dict[str, object]) -> None:
        with self._condition:
            self.incoming.append(json.dumps(event, ensure_ascii=False))
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self.closed = True
            self._condition.notify_all()


class _Connector:
    def __init__(self, socket: _FakeSocket) -> None:
        self.socket = socket
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> _FakeSocket:
        self.calls.append((url, headers, timeout))
        return self.socket


def _config(**overrides: object) -> RealtimeVoiceConfig:
    values: dict[str, object] = {
        "enabled": True,
        "provider": "qwen_audio",
        "url": "wss://workspace-123.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime",
        "model": "qwen-audio-3.0-realtime-flash",
        "api_key": "sk-private-realtime-test-only",
        "turn_detection": "server_vad",
        "threshold": 0.5,
        "silence_duration_ms": 500,
        "max_history_turns": 8,
        "timeout_seconds": 5,
    }
    values.update(overrides)
    return RealtimeVoiceConfig(**values)  # type: ignore[arg-type]


def _ready_socket() -> _FakeSocket:
    return _FakeSocket(
        [
            {"type": "session.created", "event_id": "provider-secret-created", "session": {"id": "provider-session-secret"}},
            {
                "type": "session.updated",
                "event_id": "provider-secret-updated",
                "session": {"model": "qwen-audio-3.0-realtime-flash", "modalities": ["text"]},
            },
        ]
    )


def _wait_until(predicate: object, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def _add_output_item(session: QwenRealtimeSession, response_id: str, item_id: str, item_type: str) -> None:
    session.handle_provider_event_for_test(
        {
            "type": "response.output_item.added",
            "response_id": response_id,
            "item": {"id": item_id, "type": item_type},
        }
    )


class QwenRealtimeConfigurationTests(unittest.TestCase):
    def test_only_reviewed_tls_dashscope_endpoints_are_accepted(self) -> None:
        self.assertTrue(is_safe_qwen_realtime_url("wss://dashscope.aliyuncs.com/api-ws/v1/realtime"))
        self.assertTrue(
            is_safe_qwen_realtime_url(
                "wss://space-123.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
            )
        )
        for rejected in (
            "ws://dashscope.aliyuncs.com/api-ws/v1/realtime",
            "wss://user:pass@dashscope.aliyuncs.com/api-ws/v1/realtime",
            "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?key=secret",
            "wss://dashscope.aliyuncs.com/other",
            "wss://dashscope.aliyuncs.com.evil.example/api-ws/v1/realtime",
        ):
            self.assertFalse(is_safe_qwen_realtime_url(rejected), rejected)

    def test_key_can_come_from_uncommitted_secrets_without_entering_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "config.yaml").write_text(
                "realtime_voice:\n"
                "  enabled: true\n"
                "  provider: qwen_audio\n"
                "  url: wss://dashscope.aliyuncs.com/api-ws/v1/realtime\n"
                "  model: qwen-audio-3.0-realtime-flash\n",
                encoding="utf-8",
            )
            (workspace / "secrets.yaml").write_text(
                "realtime_voice:\n  api_key: sk-private-realtime-test-only\n",
                encoding="utf-8",
            )
            _coordinator, state = build_realtime_voice_coordinator(workspace)
        self.assertTrue(state.configured)
        self.assertNotIn("sk-private", json.dumps(state.__dict__))
        self.assertEqual(state.output, "local_tts")


class QwenRealtimePersonaTests(unittest.TestCase):
    def test_attack_instruction_tokens_are_conservative(self) -> None:
        for authorized in ("帮我攻击那只僵尸", "打它", "attack the zombie", "开打吧", "kill it", "去揍它"):
            self.assertTrue(_transcript_authorizes_attack(authorized), authorized)
        for refused in ("", "打包带走", "我们接下来干嘛", "打扫一下房间", "打个招呼", "flight 延误了"):
            self.assertFalse(_transcript_authorizes_attack(refused), refused)

    def _start(self, mode: str = "conversation", persona: object = lambda: "") -> tuple[_FakeSocket, RealtimeVoiceCoordinator, dict[str, object]]:
        socket = _ready_socket()
        connector = _Connector(socket)
        coordinator = RealtimeVoiceCoordinator(
            _config(),
            connector=connector,
            persona=persona,  # type: ignore[arg-type]
        )
        events: list[dict[str, object]] = []
        result = coordinator.start("owner-persona", events.append, mode=mode, minecraft_session_id="session-mc-1")
        return socket, coordinator, result

    def _instructions(self, socket: _FakeSocket) -> str:
        updates = [message for message in socket.sent if message.get("type") == "session.update"]
        self.assertTrue(updates)
        return str(updates[0]["session"]["instructions"])

    def test_harness_persona_reaches_conversation_and_minecraft_instructions(self) -> None:
        for mode in ("conversation", "minecraft"):
            with self.subTest(mode=mode):
                socket, coordinator, result = self._start(mode=mode, persona=lambda: "角色：胆小\n语气：小声\n人设：害怕战斗\n边界：\n- 不主动攻击")
                self.assertTrue(result["ok"], result)
                instructions = self._instructions(socket)
                self.assertIn("角色设定（始终遵守）", instructions)
                self.assertIn("胆小", instructions)
                self.assertIn("不主动攻击", instructions)
                coordinator.stop("owner-persona", str(result["session_id"]))

    def test_persona_is_read_per_session_not_once_at_build_time(self) -> None:
        current = ["第一人格"]
        socket = _ready_socket()
        connector = _Connector(socket)
        coordinator = RealtimeVoiceCoordinator(_config(), connector=connector, persona=lambda: current[0])
        first = coordinator.start("owner-persona", lambda event: None)
        self.assertTrue(first["ok"], first)
        self.assertIn("第一人格", self._instructions(socket))
        coordinator.stop("owner-persona", str(first["session_id"]))
        current[0] = "第二人格"
        socket2 = _ready_socket()
        connector.socket = socket2
        second = coordinator.start("owner-persona", lambda event: None)
        self.assertTrue(second["ok"], second)
        self.assertIn("第二人格", self._instructions(socket2))

    def test_unsafe_or_oversized_persona_is_bounded_and_never_fails_the_call(self) -> None:
        persona = "坏\u0000控制字符" + ("长" * 3000)
        socket, coordinator, result = self._start(persona=lambda: persona)
        self.assertTrue(result["ok"], result)
        instructions = self._instructions(socket)
        self.assertNotIn("\u0000", instructions)
        block = instructions.split("角色设定（始终遵守）：\n", 1)[1]
        for marker in ("\n输出必须是适合直接朗读", "\n你可以在确有必要时"):
            block = block.split(marker, 1)[0]
        self.assertLessEqual(len(block), 1250)
        coordinator.stop("owner-persona", str(result["session_id"]))

    def test_missing_or_broken_persona_leaves_generic_identity(self) -> None:
        for persona in (None, lambda: (_ for _ in ()).throw(RuntimeError("broken"))):
            with self.subTest(persona=persona):
                socket, coordinator, result = self._start(persona=persona)
                self.assertTrue(result["ok"], result)
                self.assertNotIn("角色设定（始终遵守）", self._instructions(socket))
                coordinator.stop("owner-persona", str(result["session_id"]))


class QwenRealtimeSessionTests(unittest.TestCase):
    def _session(
        self,
        *,
        socket: _FakeSocket | None = None,
        events: list[dict[str, object]] | None = None,
        actions: list[tuple[str, str, dict[str, object]]] | None = None,
        cancels: list[tuple[str, str]] | None = None,
        controls: list[tuple[str, str, str]] | None = None,
        action_result: dict[str, object] | None = None,
        voice_locale: str = "",
        chat_locale: str = "",
    ) -> tuple[QwenRealtimeSession, _FakeSocket, _Connector, list[dict[str, object]]]:
        socket = socket or _ready_socket()
        emitted = events if events is not None else []
        action_rows = actions if actions is not None else []
        cancel_rows = cancels if cancels is not None else []
        control_rows = controls if controls is not None else []

        def execute(session_id: str, goal_id: str, envelope: dict[str, object], permit: object) -> dict[str, object]:
            permit.registered.set()
            permit.submitted.set()
            action_rows.append((session_id, goal_id, envelope))
            return action_result or {"ok": True, "status": "completed", "summary": "collect verified", "recovery_required": False}

        connector = _Connector(socket)
        session = QwenRealtimeSession(
            _config(),
            session_id="realtime-local-1",
            owner_id="owner-a",
            mode="minecraft",
            minecraft_session_id="session-minecraft-1",
            emit=emitted.append,
            execute_action=execute,
            cancel_action=lambda session_id, goal_id: cancel_rows.append((session_id, goal_id)) or {"ok": True},
            control_action=lambda action, session_id, goal_id: control_rows.append((action, session_id, goal_id)) or {"ok": True},
            connector=connector,
            voice_locale=voice_locale,
            chat_locale=chat_locale,
        )
        return session, socket, connector, emitted

    def test_explicit_pause_resume_cancel_controls_only_the_active_bound_goal(self) -> None:
        controls: list[tuple[str, str, str]] = []
        session, _socket, _connector, _events = self._session(controls=controls)
        self.assertTrue(session.start()["ok"])
        session._active_goal_id = "voice-goal-test"  # Core-owned; never supplied by Shell or provider.
        self.assertTrue(session.control_active_goal("pause")["ok"])
        self.assertTrue(session.control_active_goal("resume")["ok"])
        self.assertTrue(session.control_active_goal("cancel")["ok"])
        self.assertEqual(
            controls,
            [
                ("pause", "session-minecraft-1", "voice-goal-test"),
                ("resume", "session-minecraft-1", "voice-goal-test"),
                ("cancel", "session-minecraft-1", "voice-goal-test"),
            ],
        )
        session.stop()

    def test_start_keeps_key_in_header_and_configures_text_only_tools(self) -> None:
        session, socket, connector, events = self._session()
        result = session.start()
        self.assertTrue(result["ok"])
        url, headers, _timeout = connector.calls[0]
        self.assertIn("model=qwen-audio-3.0-realtime-flash", url)
        self.assertNotIn("sk-private", url)
        self.assertEqual(headers["Authorization"], "Bearer sk-private-realtime-test-only")
        update = socket.sent[0]
        self.assertEqual(update["type"], "session.update")
        config = update["session"]
        self.assertEqual(config["modalities"], ["text"])
        encoded = json.dumps(config)
        for forbidden in ("session_id", "goal_id", "approval", "confirmed_scope", "api_key", "sk-private"):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(len(config["tools"]), 14)  # 10 bridge primitives + observe_screen + attack/flee/guard
        self.assertEqual(
            events[-1],
            {"session_id": "realtime-local-1", "type": "state", "state": "listening"},
        )
        session.stop()

    def test_the_provider_writes_in_the_language_the_selected_voice_speaks(self) -> None:
        # Realtime has one text channel and it is spoken aloud, so a Chinese
        # answer under a Japanese voice is read as kanji readings, not words.
        session, socket, _connector, _events = self._session(voice_locale="ja-JP")
        self.assertTrue(session.start()["ok"])
        instructions = socket.sent[0]["session"]["instructions"]
        self.assertIn("日本語", instructions)
        session.stop()

    def test_an_unset_voice_language_leaves_the_instructions_alone(self) -> None:
        session, socket, _connector, _events = self._session()
        self.assertTrue(session.start()["ok"])
        instructions = socket.sent[0]["session"]["instructions"]
        self.assertNotIn("朗读语言", instructions)
        session.stop()

    def test_pcm_format_sequence_and_backpressure_are_strict(self) -> None:
        session, socket, _connector, _events = self._session()
        self.assertTrue(session.start()["ok"])
        audio = base64.b64encode(b"\x00\x00" * 320).decode("ascii")  # 40 ms at 16 kHz.
        self.assertTrue(session.append_audio(1, audio, sample_rate=16000, channels=1, sample_width=2)["ok"])
        _wait_until(lambda: any(row.get("type") == "input_audio_buffer.append" for row in socket.sent))
        self.assertEqual(session.append_audio(3, audio, sample_rate=16000, channels=1, sample_width=2)["error"], "realtime_audio_sequence_gap")
        self.assertEqual(session.append_audio(2, "not-base64", sample_rate=16000, channels=1, sample_width=2)["error"], "realtime_audio_invalid")
        self.assertEqual(session.append_audio(2, audio, sample_rate=48000, channels=1, sample_width=2)["error"], "realtime_audio_format_invalid")
        too_large = base64.b64encode(b"\x00\x00" * 2000).decode("ascii")
        self.assertEqual(session.append_audio(2, too_large, sample_rate=16000, channels=1, sample_width=2)["error"], "realtime_audio_chunk_invalid")
        session.stop()

    def test_public_events_drop_provider_ids_audio_and_raw_errors(self) -> None:
        session, _socket, _connector, events = self._session()
        self.assertTrue(session.start()["ok"])
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "secret-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "secret-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "secret-item"})
        session.handle_provider_event_for_test(
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "event_id": "secret-event",
                "item_id": "secret-item",
                "text": "你好",
                "stash": "世界",
            }
        )
        session.handle_provider_event_for_test(
            {"type": "response.audio.delta", "response_id": "secret-response", "delta": "secret-audio"}
        )
        session.handle_provider_event_for_test(
            {"type": "error", "event_id": "secret-error-id", "error": {"message": "raw secret detail", "code": "raw-code"}}
        )
        public = json.dumps(events, ensure_ascii=False)
        self.assertIn("你好世界", public)
        for forbidden in ("secret-event", "secret-item", "secret-response", "secret-audio", "raw secret detail", "raw-code"):
            self.assertNotIn(forbidden, public)
        self.assertIn("realtime_provider_error", public)
        session.stop()

    def test_function_call_executes_once_only_after_committed_completed_turn(self) -> None:
        actions: list[tuple[str, str, dict[str, object]]] = []
        session, socket, _connector, _events = self._session(actions=actions)
        self.assertTrue(session.start()["ok"])
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "private-response"}})
        _add_output_item(session, "private-response", "private-call-item", "function_call")
        session.handle_provider_event_for_test(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "private-response",
                "item_id": "private-call-item",
                "call_id": "private-call",
                "name": "minecraft_collect",
                "arguments": json.dumps({"block": "oak_log", "count": 2, "radius": 12, "dimension": "overworld"}),
            }
        )
        completed = {"type": "response.done", "response": {"id": "private-response", "status": "completed"}}
        session.handle_provider_event_for_test(completed)
        _wait_until(lambda: len(actions) == 1)
        session.handle_provider_event_for_test(completed)
        time.sleep(0.05)
        self.assertEqual(len(actions), 1)
        minecraft_session, goal_id, envelope = actions[0]
        self.assertEqual(minecraft_session, "session-minecraft-1")
        self.assertTrue(goal_id.startswith("voice-goal-"))
        self.assertEqual(envelope, {"final": True, "source": "voice", "intent": {"action": "collect", "block": "oak_log", "count": 2, "radius": 12, "dimension": "overworld"}})
        _wait_until(lambda: any(row.get("type") == "conversation.item.create" for row in socket.sent))
        provider_payload = json.dumps(socket.sent, ensure_ascii=False)
        self.assertNotIn("receipt", provider_payload)
        self.assertNotIn("private-response", json.dumps(actions))
        session.stop()

    def test_observe_screen_result_text_reaches_the_provider_output(self) -> None:
        actions: list[tuple[str, str, dict[str, object]]] = []
        session, socket, _connector, _events = self._session(
            actions=actions,
            action_result={
                "ok": True,
                "status": "completed",
                "summary": "screen_observed",
                "observation": "屏幕摘要：画面是一片橡树林。",
                "recovery_required": False,
            },
        )
        self.assertTrue(session.start()["ok"])
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "screen-response"}})
        _add_output_item(session, "screen-response", "screen-call-item", "function_call")
        session.handle_provider_event_for_test(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "screen-response",
                "item_id": "screen-call-item",
                "call_id": "screen-call",
                "name": "minecraft_observe_screen",
                "arguments": "{}",
            }
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "screen-response", "status": "completed"}})
        _wait_until(lambda: len(actions) == 1)
        _wait_until(lambda: any(row.get("type") == "conversation.item.create" for row in socket.sent))
        provider_payload = json.dumps(socket.sent, ensure_ascii=False)
        self.assertIn("屏幕摘要：画面是一片橡树林。", provider_payload)
        created = next(row for row in socket.sent if row.get("type") == "conversation.item.create")
        output = json.loads(created["item"]["output"])
        self.assertIn("observation", output)
        self.assertEqual(output["observation"], "屏幕摘要：画面是一片橡树林。")
        self.assertNotIn("receipt", output)
        self.assertEqual(actions[0][2], {"final": True, "source": "voice", "intent": {"action": "observe_screen"}})
        session.stop()

    def test_observe_screen_failure_carries_no_observation_text(self) -> None:
        actions: list[tuple[str, str, dict[str, object]]] = []
        session, socket, _connector, _events = self._session(
            actions=actions,
            action_result={"ok": False, "status": "failed", "summary": "not_completed", "recovery_required": False},
        )
        self.assertTrue(session.start()["ok"])
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "screen-response"}})
        _add_output_item(session, "screen-response", "screen-call-item", "function_call")
        session.handle_provider_event_for_test(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "screen-response",
                "item_id": "screen-call-item",
                "call_id": "screen-call",
                "name": "minecraft_observe_screen",
                "arguments": "{}",
            }
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "screen-response", "status": "completed"}})
        _wait_until(lambda: len(actions) == 1)
        _wait_until(lambda: any(row.get("type") == "conversation.item.create" for row in socket.sent))
        created = next(row for row in socket.sent if row.get("type") == "conversation.item.create")
        output = json.loads(created["item"]["output"])
        self.assertNotIn("observation", output)
        session.stop()

    def _drive_turn_call(
        self,
        session: QwenRealtimeSession,
        name: str,
        arguments: dict[str, object],
        suffix: str,
        transcript: str = "",
    ) -> None:
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": f"user-item-{suffix}"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": f"user-item-{suffix}"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": f"user-item-{suffix}"})
        if transcript:
            session.handle_provider_event_for_test(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": f"user-item-{suffix}",
                    "transcript": transcript,
                }
            )
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": f"response-{suffix}"}})
        _add_output_item(session, f"response-{suffix}", f"call-item-{suffix}", "function_call")
        session.handle_provider_event_for_test(
            {
                "type": "response.function_call_arguments.done",
                "response_id": f"response-{suffix}",
                "item_id": f"call-item-{suffix}",
                "call_id": f"call-{suffix}",
                "name": name,
                "arguments": json.dumps(arguments),
            }
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": f"response-{suffix}", "status": "completed"}})

    def test_attack_requires_an_explicit_instruction_in_the_same_turn(self) -> None:
        actions: list[tuple[str, str, dict[str, object]]] = []
        session, _socket, _connector, events = self._session(actions=actions)
        self.assertTrue(session.start()["ok"])
        self._drive_turn_call(session, "minecraft_attack", {"count": 1}, "a", transcript="帮我打包一下")
        _wait_until(lambda: any(event.get("type") == "game_action" and event.get("status") == "rejected" for event in events))
        self.assertEqual(len(actions), 0)
        rejected = next(event for event in events if event.get("type") == "game_action" and event.get("status") == "rejected")
        self.assertEqual(rejected["error"], "attack_requires_explicit_instruction")
        self._drive_turn_call(session, "minecraft_attack", {"count": 2}, "b", transcript="帮我攻击那只僵尸")
        _wait_until(lambda: len(actions) == 1)
        self.assertEqual(actions[0][2]["intent"]["action"], "attack")
        self.assertEqual(actions[0][2]["intent"]["count"], 2)
        session.stop()

    def test_flee_and_guard_need_no_attack_authorization(self) -> None:
        actions: list[tuple[str, str, dict[str, object]]] = []
        session, _socket, _connector, events = self._session(actions=actions)
        self.assertTrue(session.start()["ok"])
        self._drive_turn_call(session, "minecraft_flee", {"distance": 12}, "f", transcript="有怪，我害怕")
        _wait_until(lambda: len(actions) == 1)
        self.assertEqual(actions[0][2]["intent"]["action"], "flee")
        self._drive_turn_call(session, "minecraft_guard", {}, "g")
        _wait_until(lambda: len(actions) == 2)
        self.assertEqual(actions[1][2]["intent"]["action"], "guard")
        rejected = [event for event in events if event.get("type") == "game_action" and event.get("status") == "rejected"]
        self.assertEqual(rejected, [])
        session.stop()

    def test_a_previous_turns_instruction_does_not_authorize_a_later_attack(self) -> None:
        actions: list[tuple[str, str, dict[str, object]]] = []
        session, _socket, _connector, events = self._session(actions=actions)
        self.assertTrue(session.start()["ok"])
        # Turn one ends with an attack instruction but no attack proposal.
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item-1"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item-1"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item-1"})
        session.handle_provider_event_for_test(
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "user-item-1", "transcript": "帮我攻击那只僵尸"}
        )
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "response-1"}})
        _add_output_item(session, "response-1", "text-item-1", "message")
        session.handle_provider_event_for_test(
            {"type": "response.text.done", "response_id": "response-1", "item_id": "text-item-1", "text": "好的，收到。"}
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "response-1", "status": "completed"}})
        # Turn two proposes an attack on a neutral transcript: rejected.
        self._drive_turn_call(session, "minecraft_attack", {"count": 1}, "2", transcript="我们接下来干嘛")
        _wait_until(lambda: any(event.get("type") == "game_action" and event.get("status") == "rejected" for event in events))
        self.assertEqual(len(actions), 0)
        session.stop()

    def test_final_text_requires_the_registered_item_of_the_current_response(self) -> None:
        session, _socket, _connector, events = self._session()
        self.assertTrue(session.start()["ok"])
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "text-response"}})
        _add_output_item(session, "text-response", "text-item", "message")
        session.handle_provider_event_for_test(
            {"type": "response.text.delta", "response_id": "text-response", "item_id": "wrong-item", "delta": "不得公开"}
        )
        session.handle_provider_event_for_test(
            {"type": "response.text.done", "response_id": "text-response", "item_id": "text-item", "text": "可以公开"}
        )
        session.handle_provider_event_for_test(
            {"type": "response.done", "response": {"id": "text-response", "status": "completed"}}
        )
        public = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("不得公开", public)
        self.assertIn("可以公开", public)
        self.assertIn("assistant_text", public)
        session.stop()

    def _one_text_turn(self, session: QwenRealtimeSession, text: str) -> None:
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "split-response"}})
        _add_output_item(session, "split-response", "split-item", "message")
        session.handle_provider_event_for_test(
            {"type": "response.text.done", "response_id": "split-response", "item_id": "split-item", "text": text}
        )
        session.handle_provider_event_for_test(
            {"type": "response.done", "response": {"id": "split-response", "status": "completed"}}
        )

    def _channels(self, events: list[dict[str, object]]) -> tuple[str, str]:
        spoken = [row for row in events if row.get("type") == "assistant_text"]
        caption = [row for row in events if row.get("type") == "assistant_transcript" and row.get("final")]
        return str(spoken[-1]["text"]), str(caption[-1]["text"])

    def test_a_japanese_voice_can_speak_while_the_caption_stays_chinese(self) -> None:
        session, _socket, _connector, events = self._session(voice_locale="ja", chat_locale="zh")
        self.assertTrue(session.start()["ok"])
        self._one_text_turn(session, "朗读：今日の予定を見ましょう。\n字幕：我们来看看今天的安排。")
        spoken, caption = self._channels(events)
        self.assertEqual(spoken, "今日の予定を見ましょう。")
        self.assertEqual(caption, "我们来看看今天的安排。")
        session.stop()

    def test_an_answer_that_ignores_the_format_is_still_heard_and_read(self) -> None:
        session, _socket, _connector, events = self._session(voice_locale="ja", chat_locale="zh")
        self.assertTrue(session.start()["ok"])
        self._one_text_turn(session, "今日の予定を見ましょう。")
        spoken, caption = self._channels(events)
        self.assertEqual(spoken, "今日の予定を見ましょう。")
        self.assertEqual(caption, "今日の予定を見ましょう。")
        session.stop()

    def test_one_language_for_both_channels_asks_for_one_line(self) -> None:
        session, socket, _connector, events = self._session(voice_locale="ja", chat_locale="ja")
        self.assertTrue(session.start()["ok"])
        self.assertNotIn("字幕：", str(socket.sent[0]["session"]["instructions"]))
        self._one_text_turn(session, "今日の予定を見ましょう。")
        spoken, caption = self._channels(events)
        self.assertEqual(spoken, caption)
        session.stop()

    def test_partial_stale_unknown_and_multiple_calls_are_zero_action(self) -> None:
        actions: list[tuple[str, str, dict[str, object]]] = []
        session, _socket, _connector, _events = self._session(actions=actions)
        self.assertTrue(session.start()["ok"])
        # Missing committed turn.
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "r1"}})
        session.handle_provider_event_for_test(
            {"type": "response.function_call_arguments.done", "call_id": "c1", "name": "minecraft_observe", "arguments": "{}"}
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "r1", "status": "completed"}})
        # Multiple calls in one committed turn.
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item-2"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item-2"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item-2"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "r2"}})
        for call_id in ("c2", "c3"):
            _add_output_item(session, "r2", f"item-{call_id}", "function_call")
            session.handle_provider_event_for_test(
                {"type": "response.function_call_arguments.done", "response_id": "r2", "item_id": f"item-{call_id}", "call_id": call_id, "name": "minecraft_observe", "arguments": "{}"}
            )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "r2", "status": "completed"}})
        # Stale response after a newer speech epoch.
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "r3"}})
        _add_output_item(session, "r3", "item-c4", "function_call")
        session.handle_provider_event_for_test(
            {"type": "response.function_call_arguments.done", "response_id": "r3", "item_id": "item-c4", "call_id": "c4", "name": "minecraft_observe", "arguments": "{}"}
        )
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started"})
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "r3", "status": "completed"}})
        time.sleep(0.05)
        self.assertEqual(actions, [])
        session.stop()

    def test_stop_cancels_an_inflight_game_goal_without_replay(self) -> None:
        started = threading.Event()
        release = threading.Event()
        actions: list[tuple[str, str, dict[str, object]]] = []
        cancels: list[tuple[str, str]] = []

        def blocked_action(session_id: str, goal_id: str, envelope: dict[str, object], permit: object) -> dict[str, object]:
            permit.registered.set()
            permit.submitted.set()
            actions.append((session_id, goal_id, envelope))
            started.set()
            release.wait(1)
            return {"ok": False, "status": "partial", "summary": "partial", "recovery_required": True}

        socket = _ready_socket()
        connector = _Connector(socket)
        session = QwenRealtimeSession(
            _config(),
            session_id="realtime-local-1",
            owner_id="owner-a",
            mode="minecraft",
            minecraft_session_id="session-minecraft-1",
            emit=lambda _event: None,
            execute_action=blocked_action,
            cancel_action=lambda session_id, goal_id: cancels.append((session_id, goal_id)) or {"ok": True},
            connector=connector,
        )
        self.assertTrue(session.start()["ok"])
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "r"}})
        _add_output_item(session, "r", "item-c", "function_call")
        session.handle_provider_event_for_test(
            {"type": "response.function_call_arguments.done", "response_id": "r", "item_id": "item-c", "call_id": "c", "name": "minecraft_observe", "arguments": "{}"}
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "r", "status": "completed"}})
        self.assertTrue(started.wait(1))
        session.stop()
        release.set()
        self.assertEqual(len(actions), 1)
        self.assertEqual(cancels, [("session-minecraft-1", actions[0][1])])

    def test_stop_before_action_thread_entry_is_zero_action(self) -> None:
        actions: list[tuple[str, str, dict[str, object]]] = []
        session, _socket, _connector, _events = self._session(actions=actions)
        self.assertTrue(session.start()["ok"])
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "r-delayed"}})
        _add_output_item(session, "r-delayed", "item-c-delayed", "function_call")
        session.handle_provider_event_for_test(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "r-delayed",
                "item_id": "item-c-delayed",
                "call_id": "c-delayed",
                "name": "minecraft_observe",
                "arguments": "{}",
            }
        )
        delayed: list[threading.Thread] = []
        with patch("agent_companion.core.realtime_voice.threading.Thread.start", lambda thread: delayed.append(thread)):
            session.handle_provider_event_for_test(
                {"type": "response.done", "response": {"id": "r-delayed", "status": "completed"}}
            )
        self.assertEqual(len(delayed), 1)
        session.stop()
        delayed[0].run()
        self.assertEqual(actions, [])

    def test_response_and_item_correlation_drop_late_or_missing_provider_ids(self) -> None:
        actions: list[tuple[str, str, dict[str, object]]] = []
        session, _socket, _connector, events = self._session(actions=actions)
        self.assertTrue(session.start()["ok"])
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-old"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-old"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-old"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "old"}})
        _add_output_item(session, "old", "old-message", "message")
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-new"})
        session.handle_provider_event_for_test({"type": "response.text.delta", "response_id": "old", "item_id": "old-message", "delta": "旧回答"})
        session.handle_provider_event_for_test(
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "user-old", "transcript": "旧转写"}
        )
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-new"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-new"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "new"}})
        _add_output_item(session, "new", "new-call-item", "function_call")
        session.handle_provider_event_for_test(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "old",
                "item_id": "new-call-item",
                "call_id": "late-old-call",
                "name": "minecraft_observe",
                "arguments": "{}",
            }
        )
        session.handle_provider_event_for_test(
            {"type": "response.done", "response": {"id": "mismatch", "status": "completed"}}
        )
        session.handle_provider_event_for_test({"type": "response.created", "response": {}})
        session.handle_provider_event_for_test(
            {
                "type": "response.function_call_arguments.done",
                "call_id": "missing-id",
                "name": "minecraft_observe",
                "arguments": "{}",
            }
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"status": "completed"}})
        time.sleep(0.05)
        public = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("旧回答", public)
        self.assertNotIn("旧转写", public)
        self.assertEqual(actions, [])

    def test_tool_follow_up_cannot_execute_a_second_action_in_the_same_mic_turn(self) -> None:
        actions: list[tuple[str, str, dict[str, object]]] = []
        session, _socket, _connector, _events = self._session(actions=actions)
        self.assertTrue(session.start()["ok"])
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item"})
        for response_id, call_id, name in (("r-one", "c-one", "minecraft_observe"), ("r-two", "c-two", "minecraft_inventory")):
            session.handle_provider_event_for_test({"type": "response.created", "response": {"id": response_id}})
            _add_output_item(session, response_id, f"item-{call_id}", "function_call")
            session.handle_provider_event_for_test(
                {
                    "type": "response.function_call_arguments.done",
                    "response_id": response_id,
                    "item_id": f"item-{call_id}",
                    "call_id": call_id,
                    "name": name,
                    "arguments": "{}",
                }
            )
            session.handle_provider_event_for_test(
                {"type": "response.done", "response": {"id": response_id, "status": "completed"}}
            )
            _wait_until(lambda: len(actions) >= 1)
        time.sleep(0.05)
        self.assertEqual(len(actions), 1)

    def test_provider_loss_closes_socket_and_scrubs_pcm_queue(self) -> None:
        session, socket, _connector, _events = self._session()
        self.assertTrue(session.start()["ok"])
        session._audio_queue.put_nowait(b"private-pcm")
        session.handle_provider_event_for_test(
            {"type": "error", "event_id": "private-provider-error", "error": {"message": "private detail"}}
        )
        self.assertTrue(socket.closed)
        self.assertIsNone(session._socket)
        self.assertNotIn(b"private-pcm", list(session._audio_queue.queue))
        _wait_until(lambda: session._sender is not None and not session._sender.is_alive())
        audio = base64.b64encode(b"\x00\x00" * 320).decode("ascii")
        self.assertEqual(
            session.append_audio(1, audio, sample_rate=16000, channels=1, sample_width=2)["error"],
            "realtime_session_not_running",
        )


class RealtimeVoiceCoordinatorTests(unittest.TestCase):
    def test_owner_and_sequence_are_bound_to_one_session(self) -> None:
        socket = _ready_socket()
        coordinator = RealtimeVoiceCoordinator(
            _config(),
            execute_action=lambda *_: {"ok": True},
            cancel_action=lambda *_: {"ok": True},
            connector=_Connector(socket),
        )
        events: list[dict[str, object]] = []
        started = coordinator.start("owner-a", events.append, mode="conversation")
        self.assertTrue(started["ok"])
        session_id = str(started["session_id"])
        audio = base64.b64encode(b"\x00\x00" * 320).decode("ascii")
        denied = coordinator.append_audio(
            "owner-b",
            {"session_id": session_id, "sequence": 1, "sample_rate": 16000, "channels": 1, "sample_width": 2, "audio_base64": audio},
        )
        self.assertEqual(denied, {"ok": False, "error": "realtime_session_owner_mismatch"})
        self.assertFalse(coordinator.stop("owner-b", session_id)["ok"])
        self.assertTrue(coordinator.stop("owner-a", session_id)["ok"])

    def test_provider_terminal_event_retires_the_owner_session(self) -> None:
        socket = _ready_socket()
        coordinator = RealtimeVoiceCoordinator(_config(), connector=_Connector(socket))
        started = coordinator.start("owner-a", lambda _event: None, mode="conversation")
        self.assertTrue(started["ok"])
        session_id = str(started["session_id"])
        coordinator._sessions[session_id].handle_provider_event_for_test({"type": "error", "error": {}})
        self.assertEqual(coordinator.status("owner-a"), {"ok": False, "error": "realtime_session_not_found", "state": "idle"})
        self.assertTrue(socket.closed)


class RealtimeOwnerRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_ephemeral_event_is_sent_only_to_the_bound_websocket(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def send(self, message: str) -> None:
                self.messages.append(message)

        owner = Client()
        other = Client()
        bridge = JsonRpcBridge.__new__(JsonRpcBridge)
        bridge.clients = {owner, other}
        bridge._client_owners = {owner: "owner-a", other: "owner-b"}
        bridge._owner_clients = {"owner-a": owner, "owner-b": other}
        bridge.realtime_voice = type("Coordinator", (), {"stop_owner": lambda *_: None})()
        await bridge._send_to_owner("owner-a", "private-ephemeral-event")
        self.assertEqual(owner.messages, ["private-ephemeral-event"])
        self.assertEqual(other.messages, [])

    async def test_realtime_tts_drops_chunks_after_a_new_barge_epoch(self) -> None:
        bridge = JsonRpcBridge.__new__(JsonRpcBridge)
        bridge._realtime_epochs = {"realtime-session": 1}
        bridge._tts_speaker_lock = asyncio.Lock()
        sent: list[str] = []

        class Tts:
            def status_payload(self) -> dict[str, object]:
                return {"provider": "gpt-sovits", "configured": True}

            def synthesize_stream(self, _text: str, _emotion: str, _delivery: object):
                yield {
                    "voice_audio_pcm16_base64": "AAA=",
                    "voice_audio_sample_rate": 32000,
                    "voice_audio_sequence": 0,
                    "voice_audio_source": "local",
                }
                bridge._realtime_epochs["realtime-session"] = 2
                yield {
                    "voice_audio_pcm16_base64": "AAA=",
                    "voice_audio_sample_rate": 32000,
                    "voice_audio_sequence": 1,
                    "voice_audio_source": "local",
                }

        async def send(_owner: str, message: str) -> None:
            sent.append(message)

        bridge.tts = Tts()
        bridge._send_to_owner = send
        await bridge._synthesize_realtime_text(
            "owner-a",
            {"session_id": "realtime-session", "epoch": 1, "text": "你好"},
        )
        self.assertEqual(len(sent), 1)
        self.assertIn('"voice_audio_sequence": 0', sent[0])


if __name__ == "__main__":
    unittest.main()
