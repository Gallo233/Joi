from __future__ import annotations

import asyncio
import base64
import json
import queue
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_companion.core.config import RealtimeVoiceConfig, is_safe_qwen_realtime_url
from agent_companion.core.planner import build_plan, is_minecraft_task
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
        on_transcripts: object = None,
        world_memory_text: str = "",
        caption_repair: object = None,
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
            on_transcripts=on_transcripts,  # type: ignore[arg-type]
            connector=connector,
            voice_locale=voice_locale,
            chat_locale=chat_locale,
            world_memory_text=world_memory_text,
            caption_repair=caption_repair,
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
        # Every Minecraft proposal plus the two local ones every mode gets.
        names = [tool["function"]["name"] for tool in config["tools"]]
        self.assertEqual(len(names), 27)
        # The read-only lookups are what stop the model guessing recipes and
        # directions, so their presence is part of the contract.
        for expected in ("minecraft_lookup_recipe", "minecraft_inspect_container", "minecraft_locate", "minecraft_smelt"):
            self.assertIn(expected, names)
        self.assertEqual(
            [tool["function"]["name"] for tool in config["tools"]][:2],
            ["joi_play_motion", "joi_run_skill"],
        )
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
        # Forward-only, not exact: frames lost upstream resynchronize, because
        # refusing them forever left Joi listening and permanently deaf.
        self.assertTrue(session.append_audio(3, audio, sample_rate=16000, channels=1, sample_width=2)["ok"])
        # A repeat or a rewind is still refused.
        self.assertEqual(session.append_audio(3, audio, sample_rate=16000, channels=1, sample_width=2)["error"], "realtime_audio_sequence_gap")
        self.assertEqual(session.append_audio(2, audio, sample_rate=16000, channels=1, sample_width=2)["error"], "realtime_audio_sequence_gap")
        self.assertEqual(session.append_audio(4, "not-base64", sample_rate=16000, channels=1, sample_width=2)["error"], "realtime_audio_invalid")
        self.assertEqual(session.append_audio(4, audio, sample_rate=48000, channels=1, sample_width=2)["error"], "realtime_audio_format_invalid")
        too_large = base64.b64encode(b"\x00\x00" * 2000).decode("ascii")
        self.assertEqual(session.append_audio(4, too_large, sample_rate=16000, channels=1, sample_width=2)["error"], "realtime_audio_chunk_invalid")
        session.stop()

    def test_a_brief_stall_drops_audio_instead_of_dropping_the_call(self) -> None:
        """Under a second of buffer used to mean any hiccup ended the session."""

        session, _socket, _connector, events = self._session()
        self.assertTrue(session.start()["ok"])
        audio = base64.b64encode(b"\x00\x00" * 320).decode("ascii")
        # Wedge the sender so nothing drains, then push far past the queue depth.
        session._audio_queue.maxsize = 4
        for index in range(1, 40):
            result = session.append_audio(index, audio, sample_rate=16000, channels=1, sample_width=2)
            self.assertTrue(result["ok"], result)
        self.assertFalse(session.stopped)
        self.assertFalse([event for event in events if event.get("type") == "error"])
        session.stop()

    def test_a_sender_that_never_drains_is_still_a_lost_transport(self) -> None:
        class _Wedged:
            """A queue that is full and never drains: the sender is gone."""

            maxsize = 1

            def put_nowait(self, _item: object) -> None:
                raise queue.Full

            def get_nowait(self) -> object:
                raise queue.Empty

        session, _socket, _connector, _events = self._session()
        self.assertTrue(session.start()["ok"])
        audio = base64.b64encode(b"\x00\x00" * 320).decode("ascii")
        session._audio_queue = _Wedged()  # type: ignore[assignment]
        outcome = {"ok": True}
        for index in range(1, 200):
            outcome = session.append_audio(index, audio, sample_rate=16000, channels=1, sample_width=2)
            if not outcome.get("ok"):
                break
        self.assertEqual(outcome["error"], "realtime_audio_overflow")
        self.assertTrue(session.stopped)

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

    def test_a_spoken_turn_is_timed_from_silence_to_the_first_audio(self) -> None:
        """Nothing on this path was measured, so every claim about it was a guess."""

        session, _socket, _connector, events = self._session()
        self.assertTrue(session.start()["ok"])
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "response-1"}})
        _add_output_item(session, "response-1", "text-item", "message")
        session.handle_provider_event_for_test(
            {"type": "response.text.delta", "response_id": "response-1", "item_id": "text-item", "delta": "好"}
        )
        session.handle_provider_event_for_test(
            {"type": "response.text.done", "response_id": "response-1", "item_id": "text-item", "text": "好的。"}
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "response-1", "status": "completed"}})
        # No latency event until the user has actually heard something.
        self.assertFalse([event for event in events if event.get("type") == "latency"])
        session.mark_audio(1, voiced=True)
        latency = [event for event in events if event.get("type") == "latency"]
        self.assertEqual(len(latency), 1)
        for field in ("first_text_ms", "answer_ms", "voice_ms", "total_ms"):
            self.assertIsInstance(latency[0][field], int)
        # The four marks are ordered, so the spans nest.
        self.assertLessEqual(latency[0]["first_text_ms"], latency[0]["answer_ms"])
        self.assertLessEqual(latency[0]["answer_ms"], latency[0]["total_ms"])
        self.assertEqual(latency[0]["turns"], 1)
        self.assertEqual(latency[0]["p50_ms"], latency[0]["total_ms"])
        # Numbers and nothing else: no text, no provider detail, no ids.
        self.assertEqual(
            set(latency[0]) - {"session_id"},
            {"type", "epoch", "first_text_ms", "answer_ms", "voice_ms", "total_ms", "turns", "p50_ms", "p95_ms"},
        )
        session.stop()

    def test_a_muted_turn_is_closed_without_a_voice_measurement(self) -> None:
        session, _socket, _connector, events = self._session()
        self.assertTrue(session.start()["ok"])
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "response-1"}})
        _add_output_item(session, "response-1", "text-item", "message")
        session.handle_provider_event_for_test(
            {"type": "response.text.done", "response_id": "response-1", "item_id": "text-item", "text": "好的。"}
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "response-1", "status": "completed"}})
        # The local voice is unavailable, so the turn is closed without one.
        session.mark_audio(1, voiced=False)
        latency = [event for event in events if event.get("type") == "latency"]
        self.assertEqual(len(latency), 1)
        self.assertNotIn("total_ms", latency[0])
        self.assertNotIn("voice_ms", latency[0])
        # A turn with no measured end contributes no percentile.
        self.assertEqual(latency[0]["p50_ms"], 0)
        session.stop()

    def test_a_turn_that_never_started_is_not_timed(self) -> None:
        """A mark with no silence to measure from has no zero, so there is nothing to report."""

        session, _socket, _connector, events = self._session()
        self.assertTrue(session.start()["ok"])
        session.mark_audio(7, voiced=True)
        self.assertFalse([event for event in events if event.get("type") == "latency"])
        session.stop()

    def test_transcript_pairs_reach_the_sink_once_on_stop(self) -> None:
        delivered: list[list[tuple[str, str]]] = []
        session, _socket, _connector, _events = self._session(on_transcripts=lambda _sid, pairs: delivered.append(pairs))
        self.assertTrue(session.start()["ok"])
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "user-item"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "user-item"})
        session.handle_provider_event_for_test(
            {"type": "conversation.item.input_audio_transcription.completed", "item_id": "user-item", "transcript": "帮我把周围看看"}
        )
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "text-response"}})
        _add_output_item(session, "text-response", "text-item", "message")
        session.handle_provider_event_for_test(
            {"type": "response.text.done", "response_id": "text-response", "item_id": "text-item", "text": "好，我看看。"}
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "text-response", "status": "completed"}})
        session.stop()
        session.stop()
        self.assertEqual(delivered, [[("帮我把周围看看", "好，我看看。")]])

    def test_world_memory_reaches_minecraft_instructions(self) -> None:
        socket = _ready_socket()
        connector = _Connector(socket)
        coordinator = RealtimeVoiceCoordinator(
            _config(),
            connector=connector,
            world_memory=lambda session_id: "上次在世界维度 overworld，血量 16" if session_id == "session-mc-1" else "",
        )
        result = coordinator.start("owner-memory", lambda event: None, mode="minecraft", minecraft_session_id="session-mc-1")
        self.assertTrue(result["ok"], result)
        update = next(message for message in socket.sent if message.get("type") == "session.update")
        instructions = str(update["session"]["instructions"])
        self.assertIn("世界记忆（可参考", instructions)
        self.assertIn("上次在世界维度 overworld", instructions)
        coordinator.stop("owner-memory", str(result["session_id"]))

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

    def test_a_one_line_answer_is_captioned_in_the_chat_language(self) -> None:
        """A speech model answers with one utterance far more often than two.

        Captioning that utterance verbatim put Japanese on screen for a user who
        had chosen Chinese -- the exact thing the chat language setting exists
        to prevent.
        """

        repairs: list[tuple[str, str]] = []

        def repair(spoken: str, locale: str) -> str:
            repairs.append((spoken, locale))
            return "我们来看看今天的安排。"

        session, _socket, _connector, events = self._session(
            voice_locale="ja", chat_locale="zh", caption_repair=repair
        )
        self.assertTrue(session.start()["ok"])
        self._one_text_turn(session, "今日の予定を見ましょう。")
        _wait_until(lambda: any(row.get("type") == "assistant_transcript" for row in events))
        spoken, caption = self._channels(events)
        self.assertEqual(spoken, "今日の予定を見ましょう。")
        self.assertEqual(caption, "我们来看看今天的安排。")
        self.assertEqual(repairs, [("今日の予定を見ましょう。", "zh")])
        session.stop()

    def test_the_voice_never_waits_for_the_caption(self) -> None:
        # The spoken line reaches TTS before any repair is attempted.
        started = threading.Event()
        release = threading.Event()

        def slow_repair(spoken: str, locale: str) -> str:
            started.set()
            release.wait(2.0)
            return "慢一点的字幕"

        session, _socket, _connector, events = self._session(
            voice_locale="ja", chat_locale="zh", caption_repair=slow_repair
        )
        self.assertTrue(session.start()["ok"])
        self._one_text_turn(session, "今日の予定を見ましょう。")
        self.assertTrue(started.wait(2.0))
        spoken_rows = [row for row in events if row.get("type") == "assistant_text"]
        self.assertEqual(len(spoken_rows), 1)
        self.assertEqual(spoken_rows[-1]["text"], "今日の予定を見ましょう。")
        self.assertFalse([row for row in events if row.get("type") == "assistant_transcript"])
        release.set()
        _wait_until(lambda: any(row.get("type") == "assistant_transcript" for row in events))
        session.stop()

    def test_an_unavailable_repair_still_shows_something(self) -> None:
        session, _socket, _connector, events = self._session(voice_locale="ja", chat_locale="zh")
        self.assertTrue(session.start()["ok"])
        self._one_text_turn(session, "今日の予定を見ましょう。")
        _wait_until(lambda: any(row.get("type") == "assistant_transcript" for row in events))
        spoken, caption = self._channels(events)
        self.assertEqual(caption, spoken)
        session.stop()

    def test_a_caption_written_in_the_spoken_language_is_repaired(self) -> None:
        # Following the format but ignoring the language is the same failure.
        def repair(spoken: str, locale: str) -> str:
            return "我们来看看今天的安排。"

        session, _socket, _connector, events = self._session(
            voice_locale="ja", chat_locale="zh", caption_repair=repair
        )
        self.assertTrue(session.start()["ok"])
        self._one_text_turn(session, "朗读：今日の予定を見ましょう。\n字幕：今日の予定を見ましょう。")
        _wait_until(lambda: any(row.get("type") == "assistant_transcript" for row in events))
        _spoken, caption = self._channels(events)
        self.assertEqual(caption, "我们来看看今天的安排。")
        session.stop()

    def test_both_parts_on_one_line_are_still_two_channels(self) -> None:
        # Otherwise Joi reads the word "字幕" out loud.
        session, _socket, _connector, events = self._session(voice_locale="ja", chat_locale="zh")
        self.assertTrue(session.start()["ok"])
        self._one_text_turn(session, "朗读：今日の予定を見ましょう。字幕：我们来看看今天的安排。")
        _wait_until(lambda: any(row.get("type") == "assistant_transcript" for row in events))
        spoken, caption = self._channels(events)
        self.assertEqual(spoken, "今日の予定を見ましょう。")
        self.assertEqual(caption, "我们来看看今天的安排。")
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
    def test_core_timer_owns_the_guest_realtime_deadline(self) -> None:
        timers: list[object] = []
        finished: list[str] = []

        class Timer:
            def __init__(self, interval: float, callback: object, args: tuple[object, ...] = ()) -> None:
                self.interval = interval
                self.callback = callback
                self.args = args
                self.daemon = False
                self.cancelled = False
                timers.append(self)

            def start(self) -> None:
                return None

            def cancel(self) -> None:
                self.cancelled = True

            def fire(self) -> None:
                self.callback(*self.args)  # type: ignore[operator]

        socket = _ready_socket()
        coordinator = RealtimeVoiceCoordinator(
            _config(max_session_seconds=180),
            connector=_Connector(socket),
            reserve_session=lambda owner, seconds: {"ok": True, "max_seconds": min(seconds, 12)},
            finish_session=finished.append,
        )
        with patch("agent_companion.core.realtime_voice.threading.Timer", Timer):
            started = coordinator.start("owner-limited", lambda _event: None, mode="conversation")
        self.assertEqual(started["max_session_seconds"], 12)
        self.assertEqual(timers[0].interval, 12)
        timers[0].fire()  # type: ignore[attr-defined]
        self.assertEqual(coordinator.status("owner-limited")["error"], "realtime_session_not_found")
        self.assertIn("owner-limited", finished)

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


class QwenRealtimeLocalSkillTests(unittest.TestCase):
    """Natural speech reaching Joi's own body and Joi's own local skills."""

    def _session(
        self,
        *,
        mode: str = "conversation",
        run_skill: object = None,
        compile_game_plan: object = None,
        events: list[dict[str, object]] | None = None,
    ) -> tuple[QwenRealtimeSession, _FakeSocket, list[dict[str, object]]]:
        socket = _ready_socket()
        emitted = events if events is not None else []
        session = QwenRealtimeSession(
            _config(),
            session_id="realtime-local-skill",
            owner_id="owner-skill",
            mode=mode,
            minecraft_session_id="session-minecraft-1" if mode == "minecraft" else "",
            emit=emitted.append,
            execute_action=lambda *_: {"ok": True, "status": "completed"},
            cancel_action=lambda *_: {"ok": True},
            run_skill=run_skill,  # type: ignore[arg-type]
            compile_game_plan=compile_game_plan,  # type: ignore[arg-type]
            allowed_players=("Steve",) if mode == "minecraft" else (),
            connector=_Connector(socket),
        )
        self.assertTrue(session.start()["ok"])
        return session, socket, emitted

    def _call_turn(
        self,
        session: QwenRealtimeSession,
        name: str,
        arguments: str,
        *,
        transcript: str = "",
        response_id: str = "skill-response",
    ) -> None:
        """One complete microphone turn whose answer is a single tool call."""

        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": f"item-{response_id}"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": f"item-{response_id}"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": f"item-{response_id}"})
        if transcript:
            session.handle_provider_event_for_test(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": f"item-{response_id}",
                    "transcript": transcript,
                }
            )
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": response_id}})
        _add_output_item(session, response_id, f"call-item-{response_id}", "function_call")
        session.handle_provider_event_for_test(
            {
                "type": "response.function_call_arguments.done",
                "response_id": response_id,
                "item_id": f"call-item-{response_id}",
                "call_id": f"call-{response_id}",
                "name": name,
                "arguments": arguments,
            }
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": response_id, "status": "completed"}})

    def test_both_modes_offer_the_local_proposals(self) -> None:
        for mode in ("conversation", "minecraft"):
            with self.subTest(mode=mode):
                session, socket, _events = self._session(mode=mode)
                names = [tool["function"]["name"] for tool in socket.sent[0]["session"]["tools"]]
                self.assertIn("joi_play_motion", names)
                self.assertIn("joi_run_skill", names)
                self.assertEqual(any(name.startswith("minecraft_") for name in names), mode == "minecraft")
                instructions = str(socket.sent[0]["session"]["instructions"])
                self.assertNotIn("当前没有任何工具权限", instructions)
                session.stop()

    def test_a_spoken_motion_request_plays_a_local_clip_and_speaks_once(self) -> None:
        session, socket, events = self._session()
        self._call_turn(session, "joi_play_motion", json.dumps({"motion": "dance", "intensity": 0.9}), transcript="给我跳个舞")
        motions = [event for event in events if event.get("type") == "character_motion"]
        self.assertEqual(len(motions), 1)
        self.assertEqual(motions[0]["motion"]["name"], "dance")
        self.assertEqual(motions[0]["motion"]["intensity"], 0.9)
        self.assertEqual(motions[0]["motion"]["duration_ms"], 6000)
        # The motion itself is silent: the line the provider is about to write is
        # the only speech for this turn.
        self.assertFalse([event for event in events if event.get("type") == "assistant_text"])
        _wait_until(lambda: any(row.get("type") == "conversation.item.create" for row in socket.sent))
        created = next(row for row in socket.sent if row.get("type") == "conversation.item.create")
        # "started", so the line the provider writes is "I'm dancing", never
        # "I danced" while the clip has barely begun.
        self.assertEqual(
            json.loads(created["item"]["output"]),
            {"status": "started", "skill": "character_motion", "requires_confirmation": False},
        )
        self.assertTrue(any(row.get("type") == "response.create" for row in socket.sent))
        session.stop()

    def test_an_unknown_motion_name_plays_nothing(self) -> None:
        session, _socket, events = self._session()
        self._call_turn(session, "joi_play_motion", json.dumps({"motion": "backflip"}), transcript="来个后空翻")
        self.assertFalse([event for event in events if event.get("type") == "character_motion"])
        rejected = [event for event in events if event.get("type") == "skill_action"]
        self.assertEqual(rejected[-1]["status"], "rejected")
        self.assertEqual(rejected[-1]["error"], "realtime_skill_unsupported")
        session.stop()

    def test_core_receives_the_users_own_words_not_the_providers(self) -> None:
        requests: list[tuple[str, str]] = []

        def run_skill(request: str, category: str) -> dict[str, object]:
            requests.append((request, category))
            return {"ok": True, "status": "started", "skill": "computer_use", "requires_confirmation": True}

        session, socket, events = self._session(run_skill=run_skill)
        # A provider that smuggles a goal into the arguments is refused outright;
        # what Core acts on can only be the transcript of this turn.
        self._call_turn(
            session,
            "joi_run_skill",
            json.dumps({"category": "computer", "request": "delete everything"}),
            transcript="帮我打开 Chrome",
        )
        _wait_until(lambda: bool(requests))
        self.assertEqual(requests, [("帮我打开 Chrome", "other")])
        _wait_until(lambda: any(row.get("type") == "conversation.item.create" for row in socket.sent))
        output = json.loads(next(row for row in socket.sent if row.get("type") == "conversation.item.create")["item"]["output"])
        self.assertEqual(output, {"status": "started", "skill": "computer_use", "requires_confirmation": True})
        skill_events = [event for event in events if event.get("type") == "skill_action"]
        self.assertEqual(skill_events[-1]["status"], "started")
        self.assertTrue(skill_events[-1]["requires_confirmation"])
        session.stop()

    def test_a_turn_with_no_transcript_starts_nothing(self) -> None:
        requests: list[tuple[str, str]] = []
        session, _socket, events = self._session(run_skill=lambda request, category: requests.append((request, category)) or {"ok": True})
        self._call_turn(session, "joi_run_skill", json.dumps({"category": "computer"}))
        time.sleep(0.05)
        self.assertEqual(requests, [])
        self.assertEqual([event for event in events if event.get("type") == "skill_action"][-1]["error"], "realtime_skill_not_understood")
        session.stop()

    def test_a_refused_skill_is_reported_as_refused_to_both_sides(self) -> None:
        session, socket, events = self._session(run_skill=lambda *_: {"ok": False, "error": "realtime_skill_not_actionable"})
        self._call_turn(session, "joi_run_skill", "{}", transcript="我们聊聊天气吧")
        _wait_until(lambda: any(row.get("type") == "conversation.item.create" for row in socket.sent))
        output = json.loads(next(row for row in socket.sent if row.get("type") == "conversation.item.create")["item"]["output"])
        self.assertEqual(output, {"status": "rejected", "error": "realtime_skill_not_actionable"})
        self.assertEqual([event for event in events if event.get("type") == "skill_action"][-1]["status"], "rejected")
        session.stop()

    def test_a_missing_core_hook_never_claims_the_skill_ran(self) -> None:
        session, socket, events = self._session()
        self._call_turn(session, "joi_run_skill", "{}", transcript="帮我打开 Chrome")
        _wait_until(lambda: any(row.get("type") == "conversation.item.create" for row in socket.sent))
        output = json.loads(next(row for row in socket.sent if row.get("type") == "conversation.item.create")["item"]["output"])
        self.assertEqual(output["status"], "rejected")
        self.assertEqual(output["error"], "realtime_skill_unavailable")
        session.stop()

    def test_one_local_proposal_per_microphone_turn(self) -> None:
        requests: list[tuple[str, str]] = []
        session, _socket, events = self._session(
            run_skill=lambda request, category: requests.append((request, category)) or {"ok": True, "status": "started"},
        )
        self._call_turn(session, "joi_play_motion", json.dumps({"motion": "greet"}), transcript="打个招呼再帮我打开 Chrome")
        # Second call, same turn: the provider follows its own tool result with
        # another proposal. The turn already spent its one action.
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "second-response"}})
        _add_output_item(session, "second-response", "second-item", "function_call")
        session.handle_provider_event_for_test(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "second-response",
                "item_id": "second-item",
                "call_id": "second-call",
                "name": "joi_run_skill",
                "arguments": "{}",
            }
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "second-response", "status": "completed"}})
        time.sleep(0.05)
        self.assertEqual(requests, [])
        self.assertEqual(len([event for event in events if event.get("type") == "character_motion"]), 1)
        session.stop()

    def test_every_refused_call_is_answered_so_the_turn_still_speaks(self) -> None:
        """Two proposals in one turn used to produce total silence.

        "挥个手，然后帮我打开 Chrome" is ordinary phrasing, and the provider
        answers it with two calls. Refusing them without sending outputs left
        both open, no response followed, and Joi never said why.
        """

        session, socket, events = self._session()
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "u"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "u"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "u"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "r"}})
        for call_id, name in (("c1", "joi_play_motion"), ("c2", "joi_run_skill")):
            _add_output_item(session, "r", f"i-{call_id}", "function_call")
            session.handle_provider_event_for_test(
                {
                    "type": "response.function_call_arguments.done",
                    "response_id": "r",
                    "item_id": f"i-{call_id}",
                    "call_id": call_id,
                    "name": name,
                    "arguments": json.dumps({"motion": "greet"}) if name == "joi_play_motion" else "{}",
                }
            )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "r", "status": "completed"}})
        outputs = [row for row in socket.sent if row.get("type") == "conversation.item.create"]
        self.assertEqual([row["item"]["call_id"] for row in outputs], ["c1", "c2"])
        for row in outputs:
            self.assertEqual(json.loads(row["item"]["output"])["error"], "realtime_one_action_per_turn")
        # One response for the batch: one per output would have Joi say two
        # separate lines about the same refusal.
        self.assertEqual(sum(1 for row in socket.sent if row.get("type") == "response.create"), 1)
        self.assertFalse([event for event in events if event.get("type") == "character_motion"])
        session.stop()

    def test_a_refused_game_action_is_answered_instead_of_left_hanging(self) -> None:
        session, socket, events = self._session(mode="minecraft")
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "u"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "u"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "u"})
        session.handle_provider_event_for_test(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "u",
                "transcript": "我们去看看周围吧",
            }
        )
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "r"}})
        _add_output_item(session, "r", "i", "function_call")
        session.handle_provider_event_for_test(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "r",
                "item_id": "i",
                "call_id": "c",
                # Scheme A refuses this: the turn carries no attack instruction.
                "name": "minecraft_attack",
                "arguments": "{}",
            }
        )
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "r", "status": "completed"}})
        rejected = [event for event in events if event.get("type") == "game_action" and event.get("status") == "rejected"]
        self.assertEqual(rejected[-1]["error"], "attack_requires_explicit_instruction")
        _wait_until(lambda: any(row.get("type") == "conversation.item.create" for row in socket.sent))
        output = json.loads(next(row for row in socket.sent if row.get("type") == "conversation.item.create")["item"]["output"])
        self.assertEqual(output, {"status": "rejected", "error": "realtime_attack_not_authorized"})
        session.stop()

    def test_call_memory_is_bounded_without_letting_a_replay_through(self) -> None:
        session, _socket, _events = self._session()
        for index in range(300):
            session._remember_handled_call(f"call-{index}", f"digest-{index}")
        self.assertLessEqual(len(session._handled_calls), 128)
        self.assertEqual(len(session._handled_calls), len(session._handled_call_order))
        # The most recent calls -- the only ones a replay can still arrive for --
        # are the ones kept.
        self.assertIn("call-299", session._handled_calls)
        self.assertNotIn("call-0", session._handled_calls)
        session.stop()

    def test_following_the_user_needs_no_name_from_the_model(self) -> None:
        """"跟着我" names nobody, and a guessed name is refused as out of scope."""

        from agent_companion.core.realtime_voice import _proposal_from_call

        proposal = _proposal_from_call("minecraft_follow_player", "{}", ("Steve",))
        self.assertEqual(proposal["intent"]["player"], "Steve")
        self.assertEqual(_proposal_from_call("minecraft_come_to_player", "{}", ("Steve",))["intent"]["player"], "Steve")
        # With nobody authorized, or with a choice to make, Core does not pick.
        self.assertIsNone(_proposal_from_call("minecraft_follow_player", "{}", ()))
        self.assertIsNone(_proposal_from_call("minecraft_follow_player", "{}", ("Steve", "Alex")))

    def test_the_model_is_told_who_the_user_is_in_game(self) -> None:
        from agent_companion.core.realtime_voice import _minecraft_instructions

        self.assertIn("Steve", _minecraft_instructions(allowed_players=("Steve",)))
        self.assertIn("填写自己的 Minecraft 玩家名", _minecraft_instructions(allowed_players=()))

    def test_a_multi_step_goal_is_compiled_instead_of_answered_with_one_action(self) -> None:
        """"Mine enough oak, then build a crafting table" is two steps, not one."""

        compiled: list[tuple[str, str]] = []
        session, socket, events = self._session(
            mode="minecraft",
            compile_game_plan=lambda session_id, request: compiled.append((session_id, request))
            or {"ok": True, "status": "started", "requires_confirmation": True},
        )
        self._call_turn(session, "minecraft_plan", "{}", transcript="帮我去挖足够的橡木，然后建造工作台")
        _wait_until(lambda: bool(compiled))
        self.assertEqual(compiled, [("session-minecraft-1", "帮我去挖足够的橡木，然后建造工作台")])
        skill = [event for event in events if event.get("type") == "skill_action"][-1]
        self.assertEqual(skill["status"], "started")
        self.assertTrue(skill["requires_confirmation"])
        session.stop()

    def test_a_local_proposal_from_a_stale_turn_is_dropped(self) -> None:
        requests: list[tuple[str, str]] = []
        session, _socket, events = self._session(
            run_skill=lambda request, category: requests.append((request, category)) or {"ok": True, "status": "started"},
        )
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "stale"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_stopped", "item_id": "stale"})
        session.handle_provider_event_for_test({"type": "input_audio_buffer.committed", "item_id": "stale"})
        session.handle_provider_event_for_test({"type": "response.created", "response": {"id": "stale-response"}})
        _add_output_item(session, "stale-response", "stale-item", "function_call")
        session.handle_provider_event_for_test(
            {
                "type": "response.function_call_arguments.done",
                "response_id": "stale-response",
                "item_id": "stale-item",
                "call_id": "stale-call",
                "name": "joi_play_motion",
                "arguments": json.dumps({"motion": "dance"}),
            }
        )
        session.handle_provider_event_for_test({"type": "input_audio_buffer.speech_started", "item_id": "fresh"})
        session.handle_provider_event_for_test({"type": "response.done", "response": {"id": "stale-response", "status": "completed"}})
        time.sleep(0.05)
        self.assertEqual(requests, [])
        self.assertFalse([event for event in events if event.get("type") == "character_motion"])
        session.stop()


class RealtimeSkillGateTests(unittest.TestCase):
    """Core's own reading of a spoken turn, on real sentences."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        (self.workspace / "config.yaml").write_text(
            "characters:\n  - name: 测试角色\n    setting: 测试\n", encoding="utf-8"
        )
        self.bridge = JsonRpcBridge(self.workspace)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_spoken_requests_reach_the_skill_they_would_reach_when_typed(self) -> None:
        for spoken, skill in (
            ("帮我打开 Chrome", "computer_use"),
            ("在B站搜索 崩坏三", "computer_use"),
            ("点击右上角那个按钮", "computer_use"),
            ("陪我看这个视频", "screen"),
            ("你刚才看到了什么", "screen"),
            ("帮我修复这个项目的报错", "code"),
        ):
            with self.subTest(spoken=spoken):
                decision = self.bridge._realtime_skill_decision(spoken)
                self.assertTrue(decision.get("ok"), decision)
                self.assertEqual(decision.get("skill"), skill)

    def test_anything_that_touches_this_machine_still_promises_confirmation(self) -> None:
        decision = self.bridge._realtime_skill_decision("帮我打开 Chrome")
        self.assertTrue(decision["requires_confirmation"])
        # Watching is read-only, so it starts without an approval card.
        self.assertFalse(self.bridge._realtime_skill_decision("陪我看这个视频")["requires_confirmation"])

    def test_conversation_is_refused_rather_than_turned_into_a_task(self) -> None:
        submitted: list[str] = []
        with patch.object(JsonRpcBridge, "submit_user_text", side_effect=lambda text: submitted.append(text) or {"ok": True}):
            for spoken in ("我们聊聊今天的天气", "你觉得这首歌好听吗", "今天心情不错", "谢谢你陪我", ""):
                with self.subTest(spoken=spoken):
                    refusal = self.bridge._realtime_run_skill(spoken, "computer")
                    self.assertFalse(refusal.get("ok"), refusal)
                    self.assertIn(refusal["error"], {"realtime_skill_not_actionable", "realtime_skill_not_understood"})
        self.assertEqual(submitted, [])

    def test_an_accepted_turn_is_submitted_without_the_voice_waiting_for_it(self) -> None:
        submitted: list[str] = []
        release = threading.Event()

        def slow_submit(text: str) -> dict[str, object]:
            submitted.append(text)
            release.wait(2)
            return {"ok": True}

        with patch.object(JsonRpcBridge, "submit_user_text", side_effect=slow_submit):
            started = time.monotonic()
            result = self.bridge._realtime_run_skill("帮我打开 Chrome", "computer")
            elapsed = time.monotonic() - started
        self.assertEqual(result["status"], "started")
        self.assertLess(elapsed, 0.5)
        _wait_until(lambda: submitted == ["帮我打开 Chrome"])
        release.set()

    def test_a_minecraft_goal_goes_to_minecraft_not_to_another_games_skill(self) -> None:
        """OK-WW automates Wuthering Waves; it must never answer for Minecraft."""

        for spoken in ("帮我在 Minecraft 里挖点石头", "在我的世界里做个梯子", "minecraft 里跟着我"):
            with self.subTest(spoken=spoken):
                plan = build_plan(spoken)
                self.assertNotEqual(plan.intent, "game_assist")
                self.assertFalse(any(step.name == "game.ok_ww.run" for step in plan.steps))
                self.assertTrue(is_minecraft_task(spoken))
                decision = self.bridge._realtime_skill_decision(spoken)
                self.assertEqual(decision.get("skill"), "game")
                self.assertTrue(decision["requires_confirmation"])
        # And the OK-WW route still answers for its own game.
        self.assertEqual(build_plan("帮我用 OK-WW 清体力").intent, "game_assist")
        self.assertFalse(is_minecraft_task("帮我用 OK-WW 清体力"))

    def test_talking_about_minecraft_is_not_a_request_inside_it(self) -> None:
        """"我的世界" is also ordinary Chinese, and a question is not an order."""

        for spoken in (
            "Minecraft 是什么游戏",
            "我的世界好玩吗",
            "跟我聊聊 Minecraft",
            "我的世界里只有你",
            "介绍一下 minecraft 的历史",
        ):
            with self.subTest(spoken=spoken):
                self.assertFalse(is_minecraft_task(spoken))

    def test_a_minecraft_goal_with_no_world_connected_says_so(self) -> None:
        result = self.bridge.minecraft_text_goal_command("帮我在 Minecraft 里挖点石头")
        self.assertFalse(result["ok"])
        summaries = [str(event["display_card"]["summary"]) for event in result["events"]]
        self.assertTrue(any("先在游戏面板里连接世界" in summary for summary in summaries), summaries)
        # Nothing was compiled and nothing reached the world.
        self.assertFalse(any("需要确认" == str(event["display_card"]["title"]) for event in result["events"]))

    def test_a_typed_minecraft_goal_takes_the_same_route(self) -> None:
        routed: list[str] = []
        with patch.object(JsonRpcBridge, "minecraft_text_goal_command", side_effect=lambda text: routed.append(text) or {"ok": True}):
            self.bridge.submit_user_text("帮我在 Minecraft 里挖点石头")
            # A game goal that happens to say "打开" must not also leave a
            # desktop-automation session behind for work that stays in the game.
            self.bridge.submit_user_text("帮我在 Minecraft 里打开箱子")
        self.assertEqual(routed, ["帮我在 Minecraft 里挖点石头", "帮我在 Minecraft 里打开箱子"])
        self.assertFalse(self.bridge.collaboration.context().get("session_id"))

    def test_a_compiled_plan_is_shown_for_approval_and_answered_from_that_card(self) -> None:
        preview = {
            "ok": True,
            "requires_approval": True,
            "approval_id": "minecraft-plan-approval-abc",
            "plan_id": "plan-abc",
            "summary": "先看看周围再收集石头",
            "steps": [{"action": "observe"}, {"action": "collect", "block": "stone"}],
            "estimated_actions": 2,
            "estimated_changes": 2,
        }
        resolved: list[tuple[str, bool]] = []
        with patch.object(JsonRpcBridge, "_active_minecraft_session_id", return_value="session-mc-live"), \
                patch.object(self.bridge.minecraft, "plan", return_value=preview) as compile_plan:
            started = self.bridge.minecraft_text_goal_command("帮我在 Minecraft 里挖点石头")
        self.assertTrue(started["ok"], started)
        self.assertTrue(started["requires_approval"])
        self.assertEqual(compile_plan.call_args[0][0]["session_id"], "session-mc-live")
        card = next(event for event in started["events"] if event["type"] == "approval_required")
        self.assertEqual(card["agent_state"]["approval"]["approval_id"], "minecraft-plan-approval-abc")
        self.assertIn("先看看周围再收集石头", card["display_card"]["summary"])
        self.assertIn("observe", card["display_card"]["body"])

        # The same conversation card resolves it, without the Shell needing to
        # know that Minecraft keeps its own approval registry.
        with patch.object(self.bridge.minecraft, "has_pending_plan_approval", return_value=True), \
                patch.object(
                    self.bridge.minecraft,
                    "resolve_plan_approval",
                    side_effect=lambda approval_id, approved: resolved.append((approval_id, approved)) or {"ok": True, "state": "running"},
                ):
            answered = self.bridge.resolve_approval_command("minecraft-plan-approval-abc", True)
        self.assertTrue(answered["ok"], answered)
        self.assertEqual(resolved, [("minecraft-plan-approval-abc", True)])
        # The answer lands in the same turn as the card, which is what retires
        # the buttons; a fresh task id would leave them on screen forever.
        self.assertEqual({event["task_id"] for event in answered["events"]}, {card["task_id"]})

    def test_a_skill_switched_off_in_settings_is_refused_not_promised(self) -> None:
        """No approval card is coming for a disabled skill, so do not imply one."""

        from agent_companion.core.policy import PolicyDecision
        from agent_companion.core.schemas import RiskLevel

        blocked = PolicyDecision(RiskLevel.MEDIUM, False, False, "skill_disabled")
        submitted: list[str] = []
        with patch.object(self.bridge.app.policy, "classify", return_value=blocked), \
                patch.object(JsonRpcBridge, "submit_user_text", side_effect=lambda text: submitted.append(text) or {"ok": True}):
            result = self.bridge._realtime_run_skill("帮我打开 Chrome", "computer")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "realtime_skill_disabled")
        self.assertEqual(submitted, [])

    def test_consent_reports_where_a_captured_frame_actually_goes(self) -> None:
        """A vision model receives the frame itself, so consent must say so."""

        with patch.object(type(self.bridge.app), "_build_vision_summarizer", return_value=object()):
            self.assertEqual(self.bridge._realtime_voice_payload()["screen_evidence"], "vision_model")
        with patch.object(type(self.bridge.app), "_build_vision_summarizer", return_value=None):
            route = self.bridge._realtime_voice_payload()["screen_evidence"]
        self.assertEqual(route, "local_ocr" if self.bridge.minecraft.screen_cache is not None else "off")
        # A broken vision probe must not silently claim the frame stays local.
        with patch.object(type(self.bridge.app), "_build_vision_summarizer", side_effect=RuntimeError("boom")):
            self.assertIn(self.bridge._realtime_voice_payload()["screen_evidence"], {"local_ocr", "off"})

    def test_a_live_world_is_found_even_behind_another_capability_session(self) -> None:
        """The world is asked of Minecraft, not of "whichever session is newest"."""

        with patch.object(self.bridge.minecraft, "active_session_ids", return_value=["session-mc-live"]), \
                patch.object(JsonRpcBridge, "_realtime_minecraft_binding_ready", return_value=True):
            self.assertEqual(self.bridge._active_minecraft_session_id(), "session-mc-live")
        with patch.object(self.bridge.minecraft, "active_session_ids", return_value=["session-mc-dead"]), \
                patch.object(JsonRpcBridge, "_realtime_minecraft_binding_ready", return_value=False):
            self.assertEqual(self.bridge._active_minecraft_session_id(), "")

    def test_a_plan_preview_without_an_approval_id_never_becomes_a_dead_card(self) -> None:
        with patch.object(JsonRpcBridge, "_active_minecraft_session_id", return_value="session-mc-live"), \
                patch.object(self.bridge.minecraft, "plan", return_value={"ok": True, "plan_id": "p", "summary": "s", "steps": []}):
            result = self.bridge.minecraft_text_goal_command("帮我在 Minecraft 里挖点石头")
        self.assertFalse(result["ok"])
        self.assertFalse([event for event in result["events"] if event["type"] == "approval_required"])

    def test_a_plan_card_stays_answerable_across_a_conversation_reload(self) -> None:
        with patch.object(self.bridge.minecraft, "pending_plan_approval_ids", return_value=["minecraft-plan-approval-abc"]):
            self.assertIn("minecraft-plan-approval-abc", self.bridge._active_approval_ids())

    def test_an_uncompilable_goal_is_reported_without_an_internal_error_code(self) -> None:
        with patch.object(JsonRpcBridge, "_active_minecraft_session_id", return_value="session-mc-live"), \
                patch.object(self.bridge.minecraft, "plan", return_value={"ok": False, "error": "plan_compile_failed"}):
            result = self.bridge.minecraft_text_goal_command("帮我在 Minecraft 里搞点什么")
        self.assertFalse(result["ok"])
        rendered = json.dumps(result["events"], ensure_ascii=False)
        self.assertIn("拆不成可执行的步骤", rendered)
        self.assertNotIn("plan_compile_failed", rendered)

    def test_the_request_is_bounded_before_it_reaches_the_planner(self) -> None:
        submitted: list[str] = []
        with patch.object(JsonRpcBridge, "submit_user_text", side_effect=lambda text: submitted.append(text) or {"ok": True}):
            self.assertTrue(self.bridge._realtime_run_skill("帮我打开 Chrome " + "长" * 900, "computer")["ok"])
        _wait_until(lambda: bool(submitted))
        self.assertEqual(len(submitted[0]), 400)


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
