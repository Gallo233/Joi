"""Core-owned Qwen Audio Realtime transport and Minecraft proposal gate.

The Shell supplies bounded PCM frames over Joi's authenticated loopback RPC.
Only Core sees the provider credential and raw provider protocol. Qwen returns
text; the selected local GPT-SoVITS voice remains Joi's only audio output.

Provider function calls are proposals, never authority. A proposal is accepted
only after the microphone turn and provider response are both complete, then it
is canonicalized and sent through ``MinecraftGameService`` by the caller.
"""

from __future__ import annotations

import base64
import binascii
from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import queue
import re
import threading
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode
import uuid

from agent_companion.core.character_motion import MOTION_SPECS, character_motion_payload
from agent_companion.core.config import (
    QWEN_REALTIME_MODELS,
    RealtimeVoiceConfig,
    is_safe_qwen_realtime_url,
    load_app_config,
)
from agent_companion.core.language_policy import (
    CHAT_LANGUAGE_FOLLOW,
    NAMEABLE_LANGUAGES,
    display_language_policy,
    spoken_language_override,
    voice_language_label,
)
from agent_companion.core.minecraft_contract import MinecraftContractError, canonicalize_game_intent


PCM_SAMPLE_RATE = 16_000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2
MIN_PCM_CHUNK_BYTES = 640  # 20 ms
MAX_PCM_CHUNK_BYTES = 3_200  # 100 ms
MAX_PROVIDER_MESSAGE_BYTES = 256 * 1024
MAX_TRANSCRIPT_CHARS = 8_000
MAX_FUNCTION_ARGUMENT_BYTES = 64 * 1024
MAX_AUDIO_QUEUE_FRAMES = 20
# Consecutive frames dropped before the sender counts as gone rather than slow.
MAX_DROPPED_AUDIO_FRAMES = 50
MAX_SKILL_REQUEST_CHARS = 400
_LOCAL_SESSION = re.compile(r"^realtime-[a-f0-9]{16,64}$")
_MINECRAFT_SESSION = re.compile(r"^session-[A-Za-z0-9_.:-]{1,95}$")
_SKILL_LABEL = re.compile(r"^[a-z0-9_]{1,32}$")
_PLAYER_NAME = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_SPOKEN_TAG = re.compile(r"^\s*(?:朗读|朗讀|speak)\s*[:：]\s*")
_CAPTION_TAG = re.compile(r"^\s*(?:字幕|caption)\s*[:：]\s*")
_CAPTION_TAG_INLINE = re.compile(r"\s*(?:字幕|caption)\s*[:：]\s*")


class RealtimeSocket(Protocol):
    def send(self, message: str) -> None:
        ...

    def recv(self, timeout: float | None = None) -> str:
        ...

    def close(self) -> None:
        ...


Connector = Callable[[str, dict[str, str], float], RealtimeSocket]
EventSink = Callable[[dict[str, Any]], None]
@dataclass
class ActionDispatchPermit:
    """Core-local cancellation handshake for the pre-registration race."""

    cancel_requested: threading.Event = field(default_factory=threading.Event)
    entered: threading.Event = field(default_factory=threading.Event)
    registered: threading.Event = field(default_factory=threading.Event)
    submitted: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)


ActionExecutor = Callable[[str, str, dict[str, Any], ActionDispatchPermit], dict[str, Any]]
ActionCanceller = Callable[[str, str], dict[str, Any]]
ActionController = Callable[[str, str, str], dict[str, Any]]
BindingValidator = Callable[[str], bool]
TerminalSink = Callable[[str, str], None]
# (the user's own words for this turn, the category the provider guessed) ->
# what Core decided to do with them. Core re-plans and gates independently.
SkillRunner = Callable[[str, str], dict[str, Any]]
# (minecraft session, the user's own words) -> a compiled plan awaiting approval.
GamePlanCompiler = Callable[[str, str], dict[str, Any]]
# (spoken line, a resolved language code) -> the same line written in that
# language. Never "follow": the caller settles that before it calls.
CaptionRepair = Callable[[str, str], str]


@dataclass(frozen=True)
class RealtimeVoiceRuntimeState:
    enabled: bool
    configured: bool
    provider: str = ""
    model: str = ""
    output: str = "local_tts"
    timeout_seconds: int = 15
    max_session_seconds: int = 0
    error: str = ""


def _default_connector(url: str, headers: dict[str, str], timeout: float) -> RealtimeSocket:
    from websockets.sync.client import connect

    return connect(
        url,
        additional_headers=headers,
        open_timeout=timeout,
        close_timeout=min(3.0, timeout),
        max_size=MAX_PROVIDER_MESSAGE_BYTES,
        user_agent_header="Joi-Realtime/0.1",
    )


# How many finished turns the rolling latency window keeps. Enough for a P95
# that means something over a call, small enough to stay a debug aid.
_LATENCY_SAMPLES = 64
# Turns kept open while their marks arrive. Matches the transcript window: a
# turn older than this has been superseded several times over.
_LATENCY_TURNS = 8


class RealtimeTurnTimings:
    """Where a spoken turn's seconds actually go.

    Nothing was measured on this path, so every claim about it -- that the wait
    is the provider, or the local voice, or the microphone -- was a guess. This
    records four marks per turn and keeps whole milliseconds: no text, no ids,
    no provider detail, nothing that is not a number.

    The turn is finished by its first audio chunk, because that is the moment
    the user hears an answer; a muted turn finishes without one.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._open: dict[int, dict[str, float]] = {}
        self._samples: deque[dict[str, int]] = deque(maxlen=_LATENCY_SAMPLES)

    def mark(self, epoch: int, stage: str) -> dict[str, int]:
        """Record one mark. Returns the finished turn's timings, or {}."""

        if epoch < 0 or stage not in {"speech_stopped", "response_started", "first_text", "text_done", "first_audio", "muted"}:
            return {}
        now = time.monotonic()
        with self._lock:
            marks = self._open.get(epoch)
            if marks is None:
                # Only a turn that started can be timed: a mark for a turn whose
                # speech-stopped was never seen has no zero to measure from.
                if stage != "speech_stopped":
                    return {}
                marks = {}
                self._open[epoch] = marks
                while len(self._open) > _LATENCY_TURNS:
                    self._open.pop(min(self._open), None)
            marks.setdefault(stage, now)
            if stage not in {"first_audio", "muted"}:
                return {}
            self._open.pop(epoch, None)
            timings = _turn_timings(marks, voiced=stage == "first_audio")
            if timings:
                self._samples.append(timings)
            return timings

    def summary(self) -> dict[str, int]:
        with self._lock:
            totals = sorted(sample["total_ms"] for sample in self._samples if "total_ms" in sample)
            turns = len(self._samples)
        if not totals:
            return {"turns": turns, "p50_ms": 0, "p95_ms": 0}
        return {"turns": turns, "p50_ms": _percentile(totals, 50), "p95_ms": _percentile(totals, 95)}


def _turn_timings(marks: Mapping[str, float], *, voiced: bool) -> dict[str, int]:
    started = marks.get("speech_stopped")
    if started is None:
        return {}
    timings: dict[str, int] = {}
    for name, stage in (("first_text_ms", "first_text"), ("answer_ms", "text_done")):
        if stage in marks:
            timings[name] = _span_ms(started, marks[stage])
    if voiced and "first_audio" in marks:
        timings["total_ms"] = _span_ms(started, marks["first_audio"])
        if "text_done" in marks:
            timings["voice_ms"] = _span_ms(marks["text_done"], marks["first_audio"])
    return timings


def _span_ms(start: float, end: float) -> int:
    return max(0, min(int((end - start) * 1000), 600000))


def _percentile(sorted_values: list[int], percentile: int) -> int:
    if not sorted_values:
        return 0
    # Nearest-rank: with a handful of samples this reports a real measurement
    # rather than an interpolation between two of them.
    rank = max(1, min(len(sorted_values), math.ceil(percentile / 100 * len(sorted_values))))
    return int(sorted_values[rank - 1])


class QwenRealtimeSession:
    """One owner-bound, non-persistent Qwen realtime call."""

    def __init__(
        self,
        config: RealtimeVoiceConfig,
        *,
        session_id: str,
        owner_id: str,
        mode: str,
        minecraft_session_id: str,
        emit: EventSink,
        execute_action: ActionExecutor,
        cancel_action: ActionCanceller,
        control_action: ActionController | None = None,
        run_skill: SkillRunner | None = None,
        compile_game_plan: GamePlanCompiler | None = None,
        on_terminal: TerminalSink | None = None,
        on_transcripts: Callable[[str, list[tuple[str, str]]], None] | None = None,
        connector: Connector = _default_connector,
        voice_locale: str = "",
        chat_locale: str = "",
        persona: str = "",
        world_memory_text: str = "",
        allowed_players: tuple[str, ...] = (),
        skill_index: str = "",
        caption_repair: CaptionRepair | None = None,
    ) -> None:
        self.config = config
        self.session_id = str(session_id)
        self.owner_id = str(owner_id)
        self.mode = str(mode)
        self.minecraft_session_id = str(minecraft_session_id)
        self.voice_locale = str(voice_locale or "")
        self.chat_locale = str(chat_locale or "")
        self.persona = str(persona or "")
        self.world_memory_text = str(world_memory_text or "")
        self.skill_index = str(skill_index or "")
        self.allowed_players = tuple(str(name)[:32] for name in (allowed_players or ()) if str(name).strip())[:16]
        self._caption_repair = caption_repair or (lambda _spoken, _locale: "")
        self.splits_channels = _splits_channels(self.voice_locale, self.chat_locale)
        self._emit_sink = emit
        self._execute_action = execute_action
        self._cancel_action = cancel_action
        self._control_action = control_action or (lambda action, session_id, goal_id: self._cancel_action(session_id, goal_id) if action == "cancel" else {"ok": False})
        # No Core hook means Joi has no local skills to offer this session; the
        # proposal is then refused rather than silently answered as if it ran.
        self._run_skill = run_skill or (lambda _request, _category: {"ok": False, "error": "realtime_skill_unavailable"})
        self._compile_game_plan = compile_game_plan or (lambda _session, _request: {"ok": False, "error": "realtime_skill_unavailable"})
        self._on_terminal = on_terminal
        self._on_transcripts = on_transcripts
        self._transcript_pairs: list[tuple[str, str]] = []
        self._transcripts_delivered = False
        self.timings = RealtimeTurnTimings()
        self._connector = connector
        self._socket: RealtimeSocket | None = None
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=MAX_AUDIO_QUEUE_FRAMES)
        self._reader: threading.Thread | None = None
        self._sender: threading.Thread | None = None
        self._stopped = False
        self._expected_audio_sequence = 1
        self._dropped_audio_frames = 0
        self._epoch = 0
        self._turn_stopped_epoch = -1
        self._turn_committed_epoch = -1
        self._speech_item_key = ""
        self._input_item_epochs: dict[str, int] = {}
        self._input_item_order: deque[str] = deque(maxlen=64)
        self._user_final_by_epoch: dict[int, str] = {}
        self._response_open = False
        self._response_epoch = -1
        self._response_key = ""
        self._response_items: dict[str, str] = {}
        self._response_text = ""
        self._response_calls: list[dict[str, str]] = []
        self._handled_responses: deque[str] = deque(maxlen=128)
        self._handled_response_set: set[str] = set()
        self._handled_calls: dict[str, str] = {}
        self._handled_call_order: deque[str] = deque(maxlen=128)
        self._active_goal_id = ""
        self._active_goal_epoch = -1
        self._active_permit: ActionDispatchPermit | None = None
        self._last_action_epoch = -1

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def start(self) -> dict[str, Any]:
        if not self.config.is_configured:
            return {"ok": False, "error": "realtime_unconfigured"}
        if self.mode not in {"conversation", "minecraft"}:
            return {"ok": False, "error": "realtime_mode_invalid"}
        if self.mode == "minecraft" and not _MINECRAFT_SESSION.fullmatch(self.minecraft_session_id):
            return {"ok": False, "error": "minecraft_session_required"}
        socket: RealtimeSocket | None = None
        try:
            socket = self._connector(
                _provider_url(self.config),
                {"Authorization": f"Bearer {self.config.api_key.strip()}"},
                float(self.config.timeout_seconds),
            )
            first = _provider_message(socket.recv(timeout=float(self.config.timeout_seconds)))
            if first.get("type") != "session.created":
                socket.close()
                return {"ok": False, "error": _provider_start_error(first)}
            socket.send(json.dumps({"type": "session.update", "session": self._session_config()}, ensure_ascii=False, separators=(",", ":")))
            updated = _provider_message(socket.recv(timeout=float(self.config.timeout_seconds)))
            if updated.get("type") != "session.updated":
                socket.close()
                return {"ok": False, "error": _provider_start_error(updated)}
        except TimeoutError:
            _close_socket(socket)
            return {"ok": False, "error": "realtime_timeout"}
        except Exception as exc:
            _close_socket(socket)
            return {"ok": False, "error": _connection_error(exc)}
        with self._lock:
            self._socket = socket
            self._stopped = False
        self._sender = threading.Thread(target=self._send_audio_loop, name=f"qwen-audio-send-{self.session_id[-8:]}", daemon=True)
        self._reader = threading.Thread(target=self._read_loop, name=f"qwen-audio-read-{self.session_id[-8:]}", daemon=True)
        self._sender.start()
        self._reader.start()
        self._emit({"type": "state", "state": "listening"})
        return {"ok": True, "session_id": self.session_id, "state": "listening", "output": "local_tts"}

    def append_audio(
        self,
        sequence: int,
        audio_base64: str,
        *,
        sample_rate: int,
        channels: int,
        sample_width: int,
    ) -> dict[str, Any]:
        with self._lock:
            if self._stopped or self._socket is None:
                return {"ok": False, "error": "realtime_session_not_running"}
            if sample_rate != PCM_SAMPLE_RATE or channels != PCM_CHANNELS or sample_width != PCM_SAMPLE_WIDTH:
                return {"ok": False, "error": "realtime_audio_format_invalid"}
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                return {"ok": False, "error": "realtime_audio_sequence_gap"}
            # Forward-only, not exact. The Shell counts every frame it hands over
            # while Core only counted the ones it accepted, so a single rejected
            # frame desynced the two forever and every later frame was refused --
            # Joi stayed "listening" and never heard another word. A skip means
            # frames were lost upstream, which is recoverable; a repeat or a
            # rewind is not, and is still refused.
            if sequence < self._expected_audio_sequence:
                return {"ok": False, "error": "realtime_audio_sequence_gap"}
        try:
            audio = base64.b64decode(str(audio_base64 or ""), validate=True)
        except (binascii.Error, ValueError, TypeError):
            return {"ok": False, "error": "realtime_audio_invalid"}
        if len(audio) < MIN_PCM_CHUNK_BYTES or len(audio) > MAX_PCM_CHUNK_BYTES or len(audio) % 2:
            return {"ok": False, "error": "realtime_audio_chunk_invalid"}
        if not self._enqueue_audio(audio):
            self._provider_lost("realtime_audio_overflow")
            return {"ok": False, "error": "realtime_audio_overflow", "recovery_required": self.mode == "minecraft"}
        with self._lock:
            self._expected_audio_sequence = sequence + 1
        return {"ok": True, "accepted_sequence": sequence}

    def _enqueue_audio(self, audio: bytes) -> bool:
        """Queue a frame, dropping the oldest rather than dropping the call.

        The queue holds under a second of speech, so any brief stall on the
        provider socket used to end the session outright. Live audio is lossy by
        nature: a short gap in what the provider hears is recoverable, a dropped
        call is not. Only a sender that has stopped draining entirely -- a full
        queue over and over -- is treated as a lost transport.
        """

        while True:
            try:
                self._audio_queue.put_nowait(audio)
            except queue.Full:
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    pass
                with self._lock:
                    self._dropped_audio_frames += 1
                    exhausted = self._dropped_audio_frames > MAX_DROPPED_AUDIO_FRAMES
                if exhausted:
                    return False
                continue
            with self._lock:
                self._dropped_audio_frames = 0
            return True

    def stop(self, reason: str = "user_stop") -> dict[str, Any]:
        active, permit = self._mark_stopped()
        if active:
            self._cancel_active_goal(active, permit)
        self._close_transport()
        self._emit({"type": "state", "state": "idle", "reason": _safe_stop_reason(reason)})
        self._notify_terminal()
        return {"ok": True, "state": "idle"}

    def handle_provider_event_for_test(self, event: Mapping[str, Any]) -> None:
        self._handle_provider_event(dict(event))

    def _session_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "modalities": ["text"],
            "input_audio_format": "pcm",
            "max_history_turns": int(self.config.max_history_turns),
            "instructions": (
                _minecraft_instructions(self.voice_locale, self.chat_locale, self.persona, self.world_memory_text, self.allowed_players, self.skill_index)
                if self.mode == "minecraft"
                else _conversation_instructions(self.voice_locale, self.chat_locale, self.persona)
            ),
            "turn_detection": {"type": self.config.turn_detection.strip()},
        }
        if self.config.turn_detection.strip() == "server_vad":
            config["turn_detection"].update(
                {
                    "threshold": float(self.config.threshold),
                    "silence_duration_ms": int(self.config.silence_duration_ms),
                }
            )
        # Joi's own body and local skills are reachable in every mode: asking
        # her to wave or to open an app is the same request whether or not a
        # game session happens to be bound. Only the game primitives are
        # mode-scoped, because only they need a confirmed world binding.
        config["tools"] = joi_skill_proposal_tools()
        if self.mode == "minecraft":
            config["tools"] = config["tools"] + minecraft_proposal_tools()
        return config

    def _send_audio_loop(self) -> None:
        while True:
            frame = self._audio_queue.get()
            if frame is None:
                return
            if not self._send_json({"type": "input_audio_buffer.append", "audio": base64.b64encode(frame).decode("ascii")}):
                self._provider_lost("realtime_disconnected")
                return

    def _read_loop(self) -> None:
        while not self.stopped:
            socket = self._socket
            if socket is None:
                return
            try:
                event = _provider_message(socket.recv())
            except Exception:
                if not self.stopped:
                    self._provider_lost("realtime_disconnected")
                return
            self._handle_provider_event(event)

    def _handle_provider_event(self, event: dict[str, Any]) -> None:
        if self.stopped:
            return
        event_type = str(event.get("type") or "")
        if event_type == "input_audio_buffer.speech_started":
            item_key = _private_item_key(event.get("item_id"))
            with self._lock:
                self._epoch += 1
                epoch = self._epoch
                cancel_response = self._response_open
                self._turn_stopped_epoch = -1
                self._turn_committed_epoch = -1
                self._speech_item_key = item_key
                self._response_open = False
                self._response_key = ""
                self._response_items = {}
                self._response_text = ""
                self._response_calls = []
            if cancel_response:
                self._send_json({"type": "response.cancel"})
            self._emit({"type": "barge_in", "epoch": epoch})
            self._emit({"type": "state", "state": "user_speaking", "epoch": epoch})
            return
        if event_type == "input_audio_buffer.speech_stopped":
            item_key = _private_item_key(event.get("item_id"))
            with self._lock:
                if item_key and item_key == self._speech_item_key:
                    self._turn_stopped_epoch = self._epoch
                epoch = self._epoch
            self._mark_timing(epoch, "speech_stopped")
            self._emit({"type": "state", "state": "thinking", "epoch": epoch})
            return
        if event_type == "input_audio_buffer.committed":
            item_key = _private_item_key(event.get("item_id"))
            with self._lock:
                if item_key and item_key == self._speech_item_key and self._turn_stopped_epoch == self._epoch:
                    self._turn_committed_epoch = self._epoch
                    self._remember_input_item(item_key, self._epoch)
            return
        if event_type == "conversation.item.input_audio_transcription.delta":
            epoch = self._input_item_epoch(event)
            text = _bounded_transcript(f"{event.get('text') or ''}{event.get('stash') or ''}")
            if text and epoch >= 0:
                self._emit({"type": "user_transcript", "text": text, "final": False, "epoch": epoch})
            return
        if event_type == "conversation.item.input_audio_transcription.completed":
            epoch = self._input_item_epoch(event)
            text = _bounded_transcript(event.get("transcript"))
            if text and epoch >= 0:
                with self._lock:
                    self._user_final_by_epoch[epoch] = text
                    while len(self._user_final_by_epoch) > 8:
                        self._user_final_by_epoch.pop(min(self._user_final_by_epoch), None)
                self._emit({"type": "user_transcript", "text": text, "final": True, "epoch": epoch})
            return
        if event_type == "response.created":
            response = event.get("response") if isinstance(event.get("response"), Mapping) else {}
            response_key = _private_response_key(response)
            with self._lock:
                if not response_key or self._turn_committed_epoch != self._epoch or self._response_open:
                    return
                self._response_open = True
                self._response_epoch = self._epoch
                self._response_key = response_key
                self._response_items = {}
                self._response_text = ""
                self._response_calls = []
            self._mark_timing(self._epoch, "response_started")
            self._emit({"type": "state", "state": "thinking", "epoch": self._epoch})
            return
        if event_type == "response.output_item.added":
            self._capture_response_item(event)
            return
        if event_type == "response.text.delta":
            epoch = self._current_response_epoch(event, "message")
            if epoch >= 0:
                self._mark_timing(epoch, "first_text")
            # A split answer is tagged line by line, so a partial caption would
            # show the tags. It arrives whole at response.done instead.
            if self.splits_channels:
                return
            text = _bounded_assistant_text(event.get("delta"))
            if text and epoch >= 0:
                self._emit({"type": "assistant_transcript", "text": text, "final": False, "epoch": epoch})
            return
        if event_type == "response.text.done":
            with self._lock:
                if self._response_event_matches_locked(event, "message"):
                    self._response_text = _bounded_assistant_text(event.get("text"))
            return
        if event_type == "response.function_call_arguments.done":
            self._capture_function_call(event)
            return
        if event_type == "response.done":
            self._complete_response(event)
            return
        if event_type in {"response.audio.delta", "response.audio.done", "response.audio_transcript.delta", "response.audio_transcript.done"}:
            # Qwen is configured text-only. Provider audio is never forwarded
            # and can never become Joi's selected character voice.
            return
        if event_type in {"error", "conversation.item.input_audio_transcription.failed"}:
            self._provider_lost("realtime_provider_error")

    def _remember_input_item(self, item_key: str, epoch: int) -> None:
        if item_key in self._input_item_epochs:
            self._input_item_epochs[item_key] = epoch
            return
        if len(self._input_item_order) == self._input_item_order.maxlen:
            self._input_item_epochs.pop(self._input_item_order[0], None)
        self._input_item_order.append(item_key)
        self._input_item_epochs[item_key] = epoch

    def _input_item_epoch(self, event: Mapping[str, Any]) -> int:
        item_key = _private_item_key(event.get("item_id"))
        with self._lock:
            epoch = self._input_item_epochs.get(item_key, -1) if item_key else -1
            return epoch if epoch == self._epoch and not self._stopped else -1

    def _response_event_matches_locked(self, event: Mapping[str, Any], expected_item_type: str = "") -> bool:
        item_key = _private_item_key(event.get("item_id"))
        return bool(
            self._response_open
            and self._response_epoch == self._epoch
            and _private_response_id_key(event.get("response_id")) == self._response_key
            and item_key
            and self._response_items.get(item_key) == expected_item_type
        )

    def _current_response_epoch(self, event: Mapping[str, Any], expected_item_type: str) -> int:
        with self._lock:
            return self._response_epoch if self._response_event_matches_locked(event, expected_item_type) else -1

    def _capture_response_item(self, event: Mapping[str, Any]) -> None:
        item = event.get("item") if isinstance(event.get("item"), Mapping) else {}
        item_key = _private_item_key(item.get("id"))
        item_type = str(item.get("type") or "")
        with self._lock:
            if (
                self._response_open
                and _private_response_id_key(event.get("response_id")) == self._response_key
                and item_key
                and item_type in {"message", "function_call"}
                and len(self._response_items) < 8
            ):
                previous = self._response_items.get(item_key)
                if previous in {None, item_type}:
                    self._response_items[item_key] = item_type

    def _capture_function_call(self, event: Mapping[str, Any]) -> None:
        name = str(event.get("name") or "")[:80]
        call_id = str(event.get("call_id") or "")[:160]
        arguments = str(event.get("arguments") or "")
        if not call_id or not name or len(arguments.encode("utf-8", errors="ignore")) > MAX_FUNCTION_ARGUMENT_BYTES:
            return
        with self._lock:
            if self._response_event_matches_locked(event, "function_call"):
                self._response_calls.append({"call_id": call_id, "name": name, "arguments": arguments})

    def _complete_response(self, event: Mapping[str, Any]) -> None:
        response = event.get("response") if isinstance(event.get("response"), Mapping) else {}
        response_key = _private_response_key(response)
        status = str(response.get("status") or "")
        with self._lock:
            if not self._response_open or not response_key or response_key != self._response_key:
                self._response_open = False
                self._response_key = ""
                self._response_items = {}
                self._response_text = ""
                self._response_calls = []
                return
            if response_key in self._handled_response_set:
                self._response_open = False
                return
            self._remember_response(response_key)
            epoch = self._response_epoch
            current_epoch = self._epoch
            committed = self._turn_committed_epoch == epoch
            calls = list(self._response_calls)
            text = self._response_text
            self._response_open = False
            self._response_key = ""
            self._response_items = {}
        if status != "completed" or epoch != current_epoch:
            self._emit({"type": "state", "state": "listening"})
            return
        if calls:
            if not committed or len(calls) != 1 or self._last_action_epoch == epoch:
                self._reject_proposal(calls[0]["name"], "realtime_action_ambiguous")
                self._refuse_calls(epoch, calls, "realtime_one_action_per_turn")
                self._emit({"type": "state", "state": "listening"})
                return
            call = calls[0]
            if str(call["name"]) == "minecraft_plan":
                if self.mode != "minecraft":
                    self._emit({"type": "game_action", "status": "rejected", "error": "realtime_action_ambiguous"})
                    self._refuse_calls(epoch, calls, "realtime_skill_unsupported")
                    self._emit({"type": "state", "state": "listening"})
                    return
                self._dispatch_local_call(epoch, call)
                return
            if str(call["name"]).startswith("minecraft_"):
                if self.mode != "minecraft":
                    self._emit({"type": "game_action", "status": "rejected", "error": "realtime_action_ambiguous"})
                    self._refuse_calls(epoch, calls, "realtime_skill_unsupported")
                    self._emit({"type": "state", "state": "listening"})
                    return
                self._dispatch_call(epoch, call)
                return
            self._dispatch_local_call(epoch, call)
            return
        if text and committed:
            if self.splits_channels:
                spoken, caption = _split_spoken_and_caption(text)
            else:
                spoken, caption = text, text
            # Speak first, always. The voice is the low-latency channel; a
            # caption that needs repairing must never hold the audio back, and
            # this runs on the provider reader thread, which must not block.
            self._mark_timing(epoch, "text_done")
            self._emit({"type": "assistant_text", "text": spoken, "epoch": epoch, "output": "local_tts"})
            self._emit({"type": "state", "state": "assistant_speaking", "epoch": epoch})
            caption_locale = self._turn_caption_locale(epoch)
            # A caption written in the spoken language is the same failure as no
            # caption at all, whether the model skipped the format or followed
            # it with the wrong language in the second line.
            usable = bool(caption) and not (caption_locale and spoken_language_override(caption_locale, caption))
            if usable:
                self._publish_caption(epoch, spoken, caption)
            elif not caption_locale:
                # No second language this turn, so nothing to repair into: the
                # spoken line is already the line the user reads.
                self._publish_caption(epoch, spoken, spoken)
            else:
                threading.Thread(
                    target=self._repair_and_publish_caption,
                    args=(epoch, spoken, caption_locale),
                    name=f"qwen-caption-{self.session_id[-8:]}",
                    daemon=True,
                ).start()
        else:
            self._emit({"type": "state", "state": "listening", "epoch": epoch})

    def _publish_caption(self, epoch: int, spoken: str, caption: str) -> None:
        """Emit the line the user reads, and keep it for optional persistence."""

        with self._lock:
            if epoch != self._epoch:
                # The user has spoken again; this caption belongs to a turn that
                # is no longer on screen.
                return
            # M1: keep the sanitized exchange for optional persistence when the
            # session ends. Raw audio and provider IDs never join it.
            user_text = self._user_final_by_epoch.get(epoch, "")
            if user_text or caption:
                self._transcript_pairs.append((user_text, caption))
                if len(self._transcript_pairs) > 200:
                    self._transcript_pairs = self._transcript_pairs[-200:]
        self._emit({"type": "assistant_transcript", "text": caption, "final": True, "epoch": epoch})

    def _turn_caption_locale(self, epoch: int) -> str:
        """The language this turn's caption belongs in, or "" for "same as spoken".

        "follow" is not a language. It stands for whatever the user just spoke,
        which is only knowable once this turn's words exist -- so it has to be
        resolved here, per turn, and never handed on as if it were a locale.
        Passing it through was what put an English caption under a Chinese
        question: the repair call was asked to rewrite the line into a language
        named "follow", and the model picked one.

        "" means the caption needs no second language at all: the user spoke
        what the character speaks, or spoke something this module cannot name
        with confidence. Both are better served by showing the spoken line than
        by a translation into a guess.
        """

        if not _follows_user(self.chat_locale):
            return self.chat_locale
        with self._lock:
            user_text = self._user_final_by_epoch.get(epoch, "")
        code = display_language_policy(user_text).code if user_text.strip() else ""
        if code not in NAMEABLE_LANGUAGES:
            return ""
        return "" if code == _base_language(self.voice_locale) else code

    def _repair_and_publish_caption(self, epoch: int, spoken: str, caption_locale: str) -> None:
        """Write the caption in the chat language when the model gave one line.

        A speech model answers with a single utterance far more often than with
        the requested two lines, and captioning that utterance verbatim shows
        the spoken language on screen -- exactly what the chat language setting
        says should not happen. Translating it costs one short text call, off
        the audio path; if that is unavailable the spoken line is still shown,
        because a caption in the wrong language beats no caption at all.
        """

        caption = ""
        try:
            caption = _bounded_assistant_text(self._caption_repair(spoken, caption_locale))
        except Exception:
            caption = ""
        self._publish_caption(epoch, spoken, caption or spoken)

    def control_active_goal(self, action: str) -> dict[str, Any]:
        if action not in {"pause", "resume", "cancel"}:
            return {"ok": False, "error": "realtime_game_control_invalid"}
        with self._lock:
            goal_id = self._active_goal_id
            permit = self._active_permit
            stopped = self._stopped
        if stopped or not goal_id or not self.minecraft_session_id:
            return {"ok": False, "error": "realtime_game_goal_not_active"}
        if action == "cancel" and permit is not None and not permit.registered.is_set():
            result = self._cancel_active_goal(goal_id, permit)
        else:
            if permit is not None and not permit.registered.wait(timeout=0.5):
                return {"ok": False, "error": "realtime_game_goal_not_active"}
            try:
                result = self._control_action(action, self.minecraft_session_id, goal_id)
            except Exception:
                result = {"ok": False}
        if not result.get("ok"):
            return {
                "ok": False,
                "error": "realtime_game_control_failed",
                "recovery_required": bool(result.get("recovery_required") or result.get("forced_terminated")),
            }
        self._emit({"type": "state", "state": {"pause": "paused", "resume": "acting", "cancel": "listening"}[action]})
        return {"ok": True, "state": {"pause": "paused", "resume": "acting", "cancel": "cancelled"}[action]}

    def _dispatch_call(self, epoch: int, call: dict[str, str]) -> None:
        proposal = _proposal_from_call(call["name"], call["arguments"], self.allowed_players)
        digest = hashlib.sha256(f"{call['name']}\0{call['arguments']}".encode("utf-8")).hexdigest()
        refusal = ""
        with self._lock:
            previous = self._handled_calls.get(call["call_id"])
            if previous is not None:
                if previous != digest:
                    self._emit({"type": "error", "error": "realtime_call_conflict"})
                return
            self._remember_handled_call(call["call_id"], digest)
            proposed_action = str((proposal.get("intent") or {}).get("action") or "") if proposal else ""
            if proposal is None or self._active_goal_id or self._last_action_epoch == epoch or self._stopped:
                self._emit({"type": "game_action", "status": "rejected", "error": "realtime_action_invalid"})
                refusal = "realtime_skill_unsupported"
            # Scheme A: an attack is the one primitive that actively harms an
            # entity, so it never runs on the model's own initiative. The same
            # turn's final user transcript must contain an explicit attack
            # instruction; autonomy can only ever propose flee/guard.
            elif proposed_action == "attack" and not _transcript_authorizes_attack(self._user_final_by_epoch.get(epoch, "")):
                self._emit(
                    {
                        "type": "game_action",
                        "action": "attack",
                        "status": "rejected",
                        "error": "attack_requires_explicit_instruction",
                    }
                )
                refusal = "realtime_attack_not_authorized"
            else:
                goal_id = f"voice-goal-{uuid.uuid4().hex}"
                permit = ActionDispatchPermit()
                self._active_goal_id = goal_id
                self._active_goal_epoch = epoch
                self._active_permit = permit
                self._last_action_epoch = epoch
        if refusal:
            # Refused, but still answered: an unanswered call leaves the
            # provider waiting and the turn silent, so Joi could not even say
            # why she is not attacking.
            self._refuse_calls(epoch, [call], refusal)
            return
        action = proposed_action
        self._emit({"type": "game_action", "action": action, "status": "acting"})
        thread = threading.Thread(
            target=self._execute_call,
            args=(epoch, goal_id, call["call_id"], action, proposal, permit),
            name=f"qwen-game-{goal_id[-8:]}",
            daemon=True,
        )
        thread.start()

    def _execute_call(
        self,
        epoch: int,
        goal_id: str,
        call_id: str,
        action: str,
        proposal: dict[str, Any],
        permit: ActionDispatchPermit,
    ) -> None:
        permit.entered.set()
        with self._lock:
            if (
                permit.cancel_requested.is_set()
                or self._stopped
                or self._epoch != epoch
                or self._active_goal_id != goal_id
                or self._active_permit is not permit
            ):
                if self._active_goal_id == goal_id:
                    self._active_goal_id = ""
                    self._active_permit = None
                permit.done.set()
                return
        try:
            result = self._execute_action(self.minecraft_session_id, goal_id, proposal, permit)
        except Exception:
            result = {"ok": False, "status": "failed", "recovery_required": True}
        finally:
            permit.done.set()
        safe_result = _safe_action_result(action, result)
        with self._lock:
            if self._active_goal_id == goal_id:
                self._active_goal_id = ""
                self._active_permit = None
            current = not self._stopped and self._epoch == epoch
        self._emit({"type": "game_action", **safe_result})
        if not current:
            return
        if not self._send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(safe_result, ensure_ascii=False, separators=(",", ":")),
                },
            }
        ):
            self._provider_lost("realtime_disconnected")
            return
        self._send_json({"type": "response.create", "response": {"modalities": ["text"]}})

    def _dispatch_local_call(self, epoch: int, call: dict[str, str]) -> None:
        """Accept one local-skill proposal for this microphone turn.

        Same shape as the game dispatch: a call is a proposal, it is replayed
        at most once, and it only counts when the turn that produced it is
        still the current one.
        """

        name = str(call["name"])
        digest = hashlib.sha256(f"{name}\0{call['arguments']}".encode("utf-8")).hexdigest()
        with self._lock:
            previous = self._handled_calls.get(call["call_id"])
            if previous is not None:
                if previous != digest:
                    self._emit({"type": "error", "error": "realtime_call_conflict"})
                return
            self._remember_handled_call(call["call_id"], digest)
            if self._stopped or self._epoch != epoch or self._last_action_epoch == epoch:
                self._reject_proposal(name, "realtime_action_invalid")
                self._refuse_calls(epoch, [call], "realtime_one_action_per_turn")
                return
            # The provider never supplies the request text: what Joi acts on is
            # what the user actually said this turn, so a proposal can only ever
            # re-submit the user's own words.
            transcript = self._user_final_by_epoch.get(epoch, "")
            self._last_action_epoch = epoch
        if name == "joi_play_motion":
            self._perform_motion(epoch, call)
            return
        if name == "joi_run_skill":
            self._start_skill(epoch, call, transcript)
            return
        if name == "minecraft_plan":
            self._start_game_plan(epoch, call, transcript)
            return
        # A name outside the offered surface. Answer it anyway, or the provider
        # waits forever for an output it will never get and Joi says nothing.
        self._reject_proposal(name, "realtime_action_invalid")
        self._refuse_calls(epoch, [call], "realtime_skill_unsupported")

    def _perform_motion(self, epoch: int, call: dict[str, str]) -> None:
        """Play one local character motion. Nothing leaves this machine.

        A motion is rendering, not work: the Shell owns the clip, the vocabulary
        is a closed table, and the answer the provider is about to speak is the
        only speech for this turn -- so no voice line is attached here.
        """

        motion = _motion_from_call(call["arguments"])
        if motion is None:
            result = {"ok": False, "skill": "character_motion", "error": "realtime_skill_unsupported"}
        else:
            self._emit({"type": "character_motion", "motion": motion, "epoch": epoch})
            # "started", not "completed": the clip is only beginning to play, and
            # the provider is about to write the line that goes with it.
            result = {"ok": True, "status": "started", "skill": "character_motion"}
        safe = _safe_skill_result(result)
        self._emit({"type": "skill_action", **safe})
        self._reply_after_call(epoch, call["call_id"], safe)

    def _start_skill(self, epoch: int, call: dict[str, str], transcript: str) -> None:
        request = _bounded_text(transcript, MAX_SKILL_REQUEST_CHARS).strip()
        if not request:
            # Nothing was transcribed, so there are no user words to act on.
            refused = _safe_skill_result({"ok": False, "error": "realtime_skill_not_understood"})
            self._emit({"type": "skill_action", **refused})
            self._reply_after_call(epoch, call["call_id"], refused)
            return
        threading.Thread(
            target=self._run_skill_call,
            args=(epoch, call, request, _skill_category(call["arguments"])),
            name=f"qwen-skill-{self.session_id[-8:]}",
            daemon=True,
        ).start()

    def _start_game_plan(self, epoch: int, call: dict[str, str], transcript: str) -> None:
        """Compile a multi-step Minecraft goal from the user's own words.

        A single primitive per turn cannot answer "mine enough oak, then build a
        crafting table", so Joi did one thing and reported the whole request
        done. The plan goes to the same approval card a typed goal produces --
        and, as everywhere else here, the words compiled are the user's.
        """

        request = _bounded_text(transcript, MAX_SKILL_REQUEST_CHARS).strip()
        if not request:
            refused = _safe_skill_result({"ok": False, "skill": "game", "error": "realtime_skill_not_understood"})
            self._emit({"type": "skill_action", **refused})
            self._reply_after_call(epoch, call["call_id"], refused)
            return
        threading.Thread(
            target=self._run_game_plan_call,
            args=(epoch, call, request),
            name=f"qwen-mcplan-{self.session_id[-8:]}",
            daemon=True,
        ).start()

    def _run_game_plan_call(self, epoch: int, call: dict[str, str], request: str) -> None:
        with self._lock:
            current = not self._stopped and self._epoch == epoch
        if current:
            self._emit({"type": "state", "state": "acting", "epoch": epoch})
        try:
            result = self._compile_game_plan(self.minecraft_session_id, request)
        except Exception:
            result = {"ok": False, "status": "failed"}
        safe = _safe_skill_result({**result, "skill": "game"})
        self._emit({"type": "skill_action", **safe})
        self._reply_after_call(epoch, call["call_id"], safe)

    def _run_skill_call(self, epoch: int, call: dict[str, str], request: str, category: str) -> None:
        """Hand the turn to Core off the provider reader thread.

        Core answers as soon as it has decided what the request is; the plan
        itself may then sit on an approval card for as long as the user needs,
        which is exactly why this must not be waited on here.
        """

        with self._lock:
            current = not self._stopped and self._epoch == epoch
        if current:
            # Only while this is still the turn on screen: a barge-in has already
            # moved the state to user_speaking, and "acting" would strand it.
            self._emit({"type": "state", "state": "acting", "epoch": epoch})
        try:
            result = self._run_skill(request, category)
        except Exception:
            result = {"ok": False, "status": "failed"}
        safe = _safe_skill_result(result)
        self._emit({"type": "skill_action", **safe})
        self._reply_after_call(epoch, call["call_id"], safe)

    def _reply_after_call(self, epoch: int, call_id: str, output: Mapping[str, Any]) -> None:
        """Give the provider the outcome and let it speak one line about it."""

        self._answer_calls(epoch, [(call_id, dict(output))])

    def _refuse_calls(self, epoch: int, calls: list[dict[str, str]], error: str) -> None:
        """Answer every call Joi refused, so the turn still gets a spoken line.

        A provider that proposed two things at once ("挥个手，然后帮我打开
        Chrome") used to receive nothing back: both calls stayed open, no
        response followed, and the user got silence instead of being told only
        one action per turn is accepted. Every call the model makes is answered,
        refusal included.
        """

        self._answer_calls(epoch, [(call["call_id"], {"status": "rejected", "error": error}) for call in calls])

    def _answer_calls(self, epoch: int, outputs: list[tuple[str, dict[str, Any]]]) -> None:
        """Send one output per call, then exactly one response for all of them.

        One ``response.create`` for the whole batch: one per output would have
        the provider write -- and Joi speak -- a separate line for each.
        """

        with self._lock:
            current = not self._stopped and self._epoch == epoch
        if not current or not outputs:
            return
        for call_id, output in outputs:
            if not call_id:
                continue
            if not self._send_json(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(dict(output), ensure_ascii=False, separators=(",", ":")),
                    },
                }
            ):
                self._provider_lost("realtime_disconnected")
                return
        self._send_json({"type": "response.create", "response": {"modalities": ["text"]}})

    def _reject_proposal(self, name: str, error: str) -> None:
        """Refuse a proposal on the channel the Shell watches for that kind."""

        if str(name or "").startswith("joi_"):
            self._emit({"type": "skill_action", "status": "rejected", "error": error})
            return
        self._emit({"type": "game_action", "status": "rejected", "error": error})

    def _send_json(self, payload: Mapping[str, Any]) -> bool:
        with self._lock:
            socket = self._socket
            stopped = self._stopped
        if socket is None or stopped:
            return False
        try:
            with self._send_lock:
                socket.send(json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")))
            return True
        except Exception:
            return False

    def _remember_handled_call(self, call_id: str, digest: str) -> None:
        """Remember a call so it can never run twice, without growing forever.

        Bounded like the response memory beside it: a long call would otherwise
        keep every call id it ever saw. The cap is far above the number of calls
        a single microphone turn can produce, so a replay always still finds its
        entry.
        """

        if call_id in self._handled_calls:
            self._handled_calls[call_id] = digest
            return
        if len(self._handled_call_order) == self._handled_call_order.maxlen:
            self._handled_calls.pop(self._handled_call_order[0], None)
        self._handled_call_order.append(call_id)
        self._handled_calls[call_id] = digest

    def _remember_response(self, key: str) -> None:
        if not key:
            key = f"anonymous-{uuid.uuid4().hex}"
        if len(self._handled_responses) == self._handled_responses.maxlen:
            self._handled_response_set.discard(self._handled_responses[0])
        self._handled_responses.append(key)
        self._handled_response_set.add(key)

    def _provider_lost(self, error: str) -> None:
        active, permit = self._mark_stopped()
        if active:
            self._cancel_active_goal(active, permit)
        self._close_transport()
        self._emit({"type": "error", "error": error if error in {"realtime_audio_overflow", "realtime_disconnected", "realtime_provider_error"} else "realtime_disconnected"})
        self._emit({"type": "state", "state": "recovery_required" if active else "error"})
        self._notify_terminal()

    def _mark_stopped(self) -> tuple[str, ActionDispatchPermit | None]:
        with self._lock:
            if self._stopped:
                return "", None
            self._stopped = True
            return self._active_goal_id, self._active_permit

    def _cancel_active_goal(self, goal_id: str, permit: ActionDispatchPermit | None) -> dict[str, Any]:
        if not goal_id or not self.minecraft_session_id:
            return {"ok": False, "error": "realtime_game_goal_not_active"}
        if permit is not None:
            permit.cancel_requested.set()
            if not permit.entered.is_set():
                return {"ok": True, "cancelled_before_start": True}
            deadline = time.monotonic() + 1.5
            while not permit.registered.is_set() and not permit.done.is_set() and time.monotonic() < deadline:
                permit.registered.wait(timeout=0.02)
            if permit.done.is_set() and not permit.registered.is_set():
                return {"ok": True, "cancelled_before_start": True}
            if not permit.registered.is_set():
                # The service and registry both receive cancel_requested and
                # will fail closed before bridge submission once they resume.
                return {"ok": True, "cancellation_pending": True}
            while not permit.submitted.is_set() and not permit.done.is_set() and time.monotonic() < deadline:
                permit.submitted.wait(timeout=0.02)
            if permit.done.is_set() and not permit.submitted.is_set():
                return {"ok": True, "cancelled_before_submit": True}
            if not permit.submitted.is_set():
                # The bridge client performs the final cancellation check and
                # goal.submit write under the same dispatch lock as controls.
                return {"ok": True, "cancellation_pending": True}
        try:
            return self._cancel_action(self.minecraft_session_id, goal_id)
        except Exception:
            return {"ok": False, "error": "realtime_game_control_failed", "recovery_required": True}

    def _close_transport(self) -> None:
        with self._lock:
            socket = self._socket
            self._socket = None
        while True:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        try:
            self._audio_queue.put_nowait(None)
        except queue.Full:
            pass
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass

    def _notify_terminal(self) -> None:
        callback = self._on_terminal
        if callback is not None:
            try:
                callback(self.session_id, self.owner_id)
            except Exception:
                pass
        with self._lock:
            if self._transcripts_delivered or not self._transcript_pairs:
                return
            self._transcripts_delivered = True
            pairs = list(self._transcript_pairs)
        sink = self._on_transcripts
        if sink is not None:
            try:
                sink(self.session_id, pairs)
            except Exception:
                pass

    def mark_audio(self, epoch: int, *, voiced: bool) -> None:
        """Close a turn from Core: its first audio chunk, or the muted verdict.

        Only Core knows this. The provider's own audio is refused on this path,
        so the moment the user hears an answer is the moment the local voice
        hands over its first chunk.
        """

        self._mark_timing(epoch, "first_audio" if voiced else "muted")

    def _mark_timing(self, epoch: int, stage: str) -> None:
        finished = self.timings.mark(max(0, int(epoch)), stage)
        if finished:
            self._emit({"type": "latency", "epoch": max(0, int(epoch)), **finished, **self.timings.summary()})

    def _emit(self, payload: dict[str, Any]) -> None:
        # The sink receives only this closed projection. Provider identifiers,
        # raw errors and audio never cross the boundary.
        allowed = _safe_public_event(payload)
        if allowed:
            try:
                self._emit_sink({"session_id": self.session_id, **allowed})
            except Exception:
                return


class RealtimeVoiceCoordinator:
    """Owner-scoped lifecycle for ephemeral realtime sessions."""

    def __init__(
        self,
        config: RealtimeVoiceConfig,
        *,
        execute_action: ActionExecutor | None = None,
        cancel_action: ActionCanceller | None = None,
        control_action: ActionController | None = None,
        run_skill: SkillRunner | None = None,
        compile_game_plan: GamePlanCompiler | None = None,
        validate_binding: BindingValidator | None = None,
        connector: Connector = _default_connector,
        voice_locale: Callable[[], str] | None = None,
        chat_locale: Callable[[], str] | None = None,
        persona: Callable[[], str] | None = None,
        world_memory: Callable[[str], str] | None = None,
        allowed_players: Callable[[str], list[str]] | None = None,
        skill_index: Callable[[], str] | None = None,
        transcript_sink: Callable[[str, list[tuple[str, str]]], None] | None = None,
        caption_repair: CaptionRepair | None = None,
        reserve_session: Callable[[str, int], dict[str, Any]] | None = None,
        finish_session: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self._execute_action = execute_action or (lambda *_: {"ok": False, "status": "failed"})
        self._cancel_action = cancel_action or (lambda *_: {"ok": False})
        self._control_action = control_action
        self._run_skill = run_skill
        self._compile_game_plan = compile_game_plan
        self._validate_binding = validate_binding or (lambda _session_id: True)
        self._connector = connector
        # Read per session, not once at build time: the user can switch
        # character, voice language or chat language between two realtime calls.
        self._voice_locale = voice_locale or (lambda: "")
        self._chat_locale = chat_locale or (lambda: "")
        self._persona = persona or (lambda: "")
        self._world_memory = world_memory or (lambda _session_id: "")
        self._allowed_players = allowed_players or (lambda _session_id: [])
        self._skill_index = skill_index or (lambda: "")
        self._transcript_sink = transcript_sink
        self._caption_repair = caption_repair
        self._reserve_session = reserve_session
        self._finish_session = finish_session
        self._lock = threading.RLock()
        self._sessions: dict[str, QwenRealtimeSession] = {}
        self._owner_sessions: dict[str, str] = {}
        self._session_timers: dict[str, threading.Timer] = {}

    def start(
        self,
        owner_id: str,
        emit: EventSink,
        *,
        mode: str = "conversation",
        minecraft_session_id: str = "",
    ) -> dict[str, Any]:
        owner = str(owner_id or "")
        if not owner:
            return {"ok": False, "error": "realtime_owner_required"}
        if mode == "minecraft" and (not _MINECRAFT_SESSION.fullmatch(minecraft_session_id) or not self._validate_binding(minecraft_session_id)):
            return {"ok": False, "error": "minecraft_session_not_runnable"}
        with self._lock:
            existing_id = self._owner_sessions.get(owner, "")
        if existing_id:
            self.stop(owner, existing_id)
        max_seconds = max(0, int(self.config.max_session_seconds or 0))
        if self._reserve_session is not None:
            quota = self._reserve_session(owner, max_seconds)
            if not quota.get("ok"):
                return {"ok": False, "error": str(quota.get("error") or "guest_realtime_budget_exceeded")}
            max_seconds = max(1, int(quota.get("max_seconds") or max_seconds or 1))
        session_id = f"realtime-{uuid.uuid4().hex}"
        def session_emit(payload: dict[str, Any]) -> None:
            emit({"session_id": session_id, **payload})

        session = QwenRealtimeSession(
            self.config,
            session_id=session_id,
            owner_id=owner,
            mode=mode,
            minecraft_session_id=minecraft_session_id,
            emit=session_emit,
            execute_action=self._execute_action,
            cancel_action=self._cancel_action,
            control_action=self._control_action,
            run_skill=self._run_skill,
            compile_game_plan=self._compile_game_plan,
            on_terminal=self._retire_session,
            on_transcripts=self._transcript_sink,
            connector=self._connector,
            voice_locale=_safe_voice_locale(self._voice_locale),
            chat_locale=_safe_voice_locale(self._chat_locale),
            persona=_safe_persona(self._persona),
            world_memory_text=_safe_world_memory(self._world_memory, minecraft_session_id),
            allowed_players=_safe_allowed_players(self._allowed_players, minecraft_session_id),
            skill_index=_safe_persona(self._skill_index),
            caption_repair=self._caption_repair,
        )
        with self._lock:
            self._sessions[session_id] = session
            self._owner_sessions[owner] = session_id
        result = session.start()
        if not result.get("ok"):
            self._retire_session(session_id, owner)
            return result
        if max_seconds:
            timer = threading.Timer(max_seconds, self.stop_owner, args=(owner, "guest_time_limit"))
            timer.daemon = True
            with self._lock:
                self._session_timers[session_id] = timer
            timer.start()
            result["max_session_seconds"] = max_seconds
        return result

    def append_audio(self, owner_id: str, params: Mapping[str, Any]) -> dict[str, Any]:
        required = {"session_id", "sequence", "sample_rate", "channels", "sample_width", "audio_base64"}
        if set(params) != required:
            return {"ok": False, "error": "realtime_audio_envelope_invalid"}
        session_id = str(params.get("session_id") or "")
        session, error = self._owned_session(owner_id, session_id)
        if error:
            return {"ok": False, "error": error}
        return session.append_audio(
            params.get("sequence"),  # type: ignore[arg-type]
            str(params.get("audio_base64") or ""),
            sample_rate=params.get("sample_rate"),  # type: ignore[arg-type]
            channels=params.get("channels"),  # type: ignore[arg-type]
            sample_width=params.get("sample_width"),  # type: ignore[arg-type]
        )

    def stop(self, owner_id: str, session_id: str) -> dict[str, Any]:
        session, error = self._owned_session(owner_id, session_id)
        if error:
            return {"ok": False, "error": error}
        result = session.stop()
        with self._lock:
            timer = self._session_timers.pop(session_id, None)
            self._sessions.pop(session_id, None)
            if self._owner_sessions.get(owner_id) == session_id:
                self._owner_sessions.pop(owner_id, None)
        if timer is not None:
            timer.cancel()
        if self._finish_session is not None:
            self._finish_session(owner_id)
        return result

    def stop_owner(self, owner_id: str, reason: str = "transport_lost") -> None:
        with self._lock:
            session_id = self._owner_sessions.get(owner_id, "")
        if not session_id:
            return
        session, error = self._owned_session(owner_id, session_id)
        if not error:
            session.stop(reason)
        with self._lock:
            timer = self._session_timers.pop(session_id, None)
            self._sessions.pop(session_id, None)
            self._owner_sessions.pop(owner_id, None)
        if timer is not None:
            timer.cancel()
        if self._finish_session is not None:
            self._finish_session(owner_id)

    def status(self, owner_id: str, session_id: str = "") -> dict[str, Any]:
        with self._lock:
            resolved = session_id or self._owner_sessions.get(owner_id, "")
        session, error = self._owned_session(owner_id, resolved)
        if error:
            return {"ok": False, "error": error, "state": "idle"}
        return {"ok": True, "session_id": resolved, "state": "idle" if session.stopped else "listening", "output": "local_tts"}

    def mark_audio(self, session_id: str, epoch: int, *, voiced: bool) -> None:
        """Close a turn's timing from the synthesis side. Never raises: this is a debug aid."""

        with self._lock:
            session = self._sessions.get(str(session_id or ""))
        if session is None:
            return
        session.mark_audio(max(0, int(epoch)), voiced=voiced)

    def control(self, owner_id: str, session_id: str, action: str) -> dict[str, Any]:
        session, error = self._owned_session(owner_id, session_id)
        if error:
            return {"ok": False, "error": error}
        return session.control_active_goal(action)

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            owners = list(self._owner_sessions)
            timers = list(self._session_timers.values())
            self._sessions.clear()
            self._owner_sessions.clear()
            self._session_timers.clear()
        for timer in timers:
            timer.cancel()
        for session in sessions:
            session.stop("shutdown")
        if self._finish_session is not None:
            for owner_id in owners:
                self._finish_session(owner_id)

    def _owned_session(self, owner_id: str, session_id: str) -> tuple[QwenRealtimeSession, str]:
        with self._lock:
            session = self._sessions.get(str(session_id or ""))
        if session is None:
            return _NullRealtimeSession(), "realtime_session_not_found"  # type: ignore[return-value]
        if session.owner_id != str(owner_id or ""):
            return _NullRealtimeSession(), "realtime_session_owner_mismatch"  # type: ignore[return-value]
        return session, ""

    def _retire_session(self, session_id: str, owner_id: str) -> None:
        with self._lock:
            timer = self._session_timers.pop(session_id, None)
            self._sessions.pop(session_id, None)
            if self._owner_sessions.get(owner_id) == session_id:
                self._owner_sessions.pop(owner_id, None)
        if timer is not None:
            timer.cancel()
        if self._finish_session is not None:
            self._finish_session(owner_id)


class _NullRealtimeSession:
    pass


def _safe_voice_locale(source: Callable[[], str]) -> str:
    """A missing or broken voice lookup leaves the instructions unchanged."""

    try:
        locale = str(source() or "").strip()
    except Exception:
        return ""
    return locale[:16] if locale.replace("-", "").replace("_", "").isalnum() else ""


def _safe_persona(source: Callable[[], str]) -> str:
    """Read the character harness per session, sanitized and bounded.

    A missing or broken persona callback leaves the instructions unchanged:
    the voice must still work with the generic Joi identity.
    """

    try:
        text = str(source() or "")
    except Exception:
        return ""
    return _bounded_text(text, 1_200).strip()


def _safe_world_memory(source: Callable[[str], str], session_id: str) -> str:
    """Read the per-world memory for this Minecraft session, sanitized.

    A missing, broken or empty memory just means the voice starts without it.
    """

    try:
        text = str(source(session_id) or "")
    except Exception:
        return ""
    return _bounded_text(text, 500).strip()


def _safe_allowed_players(source: Callable[[str], list[str]], session_id: str) -> tuple[str, ...]:
    """Who this session may follow, read per session and bounded."""

    try:
        rows = source(session_id) or []
    except Exception:
        return ()
    return tuple(str(name).strip()[:32] for name in list(rows)[:16] if _PLAYER_NAME.fullmatch(str(name).strip()))


def build_realtime_voice_coordinator(
    workspace: Path,
    *,
    execute_action: ActionExecutor | None = None,
    cancel_action: ActionCanceller | None = None,
    control_action: ActionController | None = None,
    run_skill: SkillRunner | None = None,
    compile_game_plan: GamePlanCompiler | None = None,
    validate_binding: BindingValidator | None = None,
    connector: Connector = _default_connector,
    voice_locale: Callable[[], str] | None = None,
    chat_locale: Callable[[], str] | None = None,
    persona: Callable[[], str] | None = None,
    world_memory: Callable[[str], str] | None = None,
    allowed_players: Callable[[str], list[str]] | None = None,
    skill_index: Callable[[], str] | None = None,
    transcript_sink: Callable[[str, list[tuple[str, str]]], None] | None = None,
    caption_repair: CaptionRepair | None = None,
    reserve_session: Callable[[str, int], dict[str, Any]] | None = None,
    finish_session: Callable[[str], None] | None = None,
) -> tuple[RealtimeVoiceCoordinator, RealtimeVoiceRuntimeState]:
    config_path = workspace / "config.yaml"
    if not config_path.is_file():
        config = RealtimeVoiceConfig()
        return RealtimeVoiceCoordinator(config), RealtimeVoiceRuntimeState(False, False, error="realtime_unconfigured")
    try:
        config = load_app_config(config_path).realtime_voice
    except Exception:
        config = RealtimeVoiceConfig()
        return RealtimeVoiceCoordinator(config), RealtimeVoiceRuntimeState(False, False, error="realtime_config_error")
    state = RealtimeVoiceRuntimeState(
        enabled=config.enabled,
        configured=config.is_configured,
        provider="qwen_audio" if config.is_configured else "",
        model=config.model if config.is_configured else "",
        output="local_tts",
        timeout_seconds=config.timeout_seconds,
        max_session_seconds=config.max_session_seconds,
        error="" if config.is_configured else "realtime_unconfigured" if not config.enabled else "realtime_config_error",
    )
    return (
        RealtimeVoiceCoordinator(
            config,
            execute_action=execute_action,
            cancel_action=cancel_action,
            control_action=control_action,
            run_skill=run_skill,
            compile_game_plan=compile_game_plan,
            validate_binding=validate_binding,
            connector=connector,
            voice_locale=voice_locale,
            chat_locale=chat_locale,
            persona=persona,
            world_memory=world_memory,
            allowed_players=allowed_players,
            skill_index=skill_index,
            transcript_sink=transcript_sink,
            caption_repair=caption_repair,
            reserve_session=reserve_session,
            finish_session=finish_session,
        ),
        state,
    )


SKILL_CATEGORIES = ("computer", "browser", "screen", "code", "game", "other")
# Every refusal the Shell and the provider may be told about, by name.
SAFE_SKILL_ERRORS = frozenset(
    {
        "realtime_skill_unavailable",
        "realtime_skill_unsupported",
        "realtime_skill_not_understood",
        "realtime_skill_not_actionable",
        "realtime_skill_disabled",
        "realtime_one_action_per_turn",
        "realtime_attack_not_authorized",
    }
)


def _function_tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def joi_skill_proposal_tools() -> list[dict[str, Any]]:
    """Two proposals: Joi's own body, and Joi's own local skills.

    Neither carries the request text. ``joi_run_skill`` deliberately takes no
    free-form goal: Core re-reads the turn's own transcript, so the provider can
    say "this turn was work" but never *what* the work is. That keeps the one
    thing a cloud model must not own -- what runs on this machine -- on this
    machine, while still letting ordinary speech reach the skills.
    """

    return [
        _function_tool(
            "joi_play_motion",
            "Play one local character motion (animation) when the user asks Joi to move, "
            "wave, dance, celebrate, pose, or stand still. Local rendering only; it changes "
            "nothing outside the character view.",
            {
                "motion": {"type": "string", "enum": sorted(MOTION_SPECS)},
                "intensity": {"type": "number", "minimum": 0.25, "maximum": 1.0},
            },
            ["motion"],
        ),
        _function_tool(
            "joi_run_skill",
            "Hand this turn to Joi's local skills when the user asked for something to be "
            "done on this computer -- open an app, click or type, search the web, look at the "
            "current screen or video, or write and fix code. Core re-plans the user's own "
            "words, checks policy, and asks the user to confirm anything that touches the "
            "machine. Never call this for ordinary conversation.",
            {"category": {"type": "string", "enum": list(SKILL_CATEGORIES)}},
            [],
        ),
    ]


def minecraft_proposal_tools() -> list[dict[str, Any]]:
    """Ten strict proposal schemas, with no Core identity or authority fields."""

    dimension = {"type": "string", "enum": ["overworld", "the_nether", "the_end"]}
    identifier = {"type": "string", "pattern": r"^[a-z0-9_:./-]{1,80}$"}
    player = {"type": "string", "pattern": r"^[A-Za-z0-9_]{1,32}$"}

    def tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return _function_tool(f"minecraft_{name}", description, properties, required)

    block_props = {
        "block": identifier,
        "count": {"type": "integer", "minimum": 1, "maximum": 64},
        "radius": {"type": "integer", "minimum": 1, "maximum": 64},
        "dimension": dimension,
    }
    return [
        tool("observe", "Observe nearby Minecraft state without changing it.", {"dimension": dimension, "radius": {"type": "integer", "minimum": 1, "maximum": 32}}, []),
        tool("inventory", "Read Joi's Minecraft inventory without changing it.", {}, []),
        # player is optional on purpose: "跟着我" names nobody, and Core fills
        # in the allowed player rather than letting the model guess a name
        # that would then be refused as out of scope.
        tool("follow_player", "Follow the user (or a named allowed player) for a bounded duration. Omit player to follow the user.", {"player": player, "distance": {"type": "integer", "minimum": 2, "maximum": 12}, "duration_seconds": {"type": "integer", "minimum": 1, "maximum": 300}}, []),
        tool("come_to_player", "Move near the user (or a named allowed player). Omit player to come to the user.", {"player": player, "distance": {"type": "integer", "minimum": 1, "maximum": 12}}, []),
        tool("collect", "Collect an allowed block and verify it reached inventory.", block_props, ["block"]),
        tool("mine", "Mine an allowed block and verify the world changed.", block_props, ["block"]),
        tool("craft", "Craft an item from available inventory.", {"item": identifier, "count": {"type": "integer", "minimum": 1, "maximum": 64}}, ["item"]),
        tool("eat", "Eat an available food item.", {"item": identifier}, []),
        tool(
            "place_blueprint",
            "Place a bounded relative blueprint using allowed blocks only.",
            {
                "anchor": {"type": "string", "enum": ["bot", "player"]},
                "player": player,
                "dimension": dimension,
                "blocks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 128,
                    "items": {
                        "type": "object",
                        "properties": {
                            "offset": {"type": "array", "items": {"type": "integer", "minimum": -64, "maximum": 64}, "minItems": 3, "maxItems": 3},
                            "block": identifier,
                        },
                        "required": ["offset", "block"],
                        "additionalProperties": False,
                    },
                },
            },
            ["anchor", "blocks"],
        ),
        tool(
            "deposit",
            "Deposit bounded item counts into an allowed nearby container.",
            {
                "container": {"type": "string", "enum": ["chest", "barrel", "shulker_box"]},
                "radius": {"type": "integer", "minimum": 1, "maximum": 16},
                "dimension": dimension,
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 16,
                    "items": {
                        "type": "object",
                        "properties": {"item": identifier, "count": {"type": "integer", "minimum": 1, "maximum": 64}},
                        "required": ["item", "count"],
                        "additionalProperties": False,
                    },
                },
            },
            ["items"],
        ),
        tool(
            "observe_screen",
            "Read a sanitized summary of the game screen Joi can see right now, without changing anything.",
            {},
            [],
        ),
        tool(
            "attack",
            "Attack the nearest hostile mob within radius. Never targets players. Only allowed after the user explicitly instructed an attack.",
            {
                "count": {"type": "integer", "minimum": 1, "maximum": 16},
                "radius": {"type": "integer", "minimum": 1, "maximum": 32},
                "dimension": dimension,
            },
            [],
        ),
        tool(
            "flee",
            "Move away from nearby hostile mobs for a bounded duration.",
            {
                "distance": {"type": "integer", "minimum": 4, "maximum": 32},
                "duration_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                "dimension": dimension,
            },
            [],
        ),
        tool(
            "guard",
            "Stop moving and stay alert where Joi stands.",
            {"dimension": dimension},
            [],
        ),
        tool("lookup_recipe", "Read what an item is crafted from and what Joi already has. Changes nothing; use it before crafting instead of guessing.", {"item": identifier}, ["item"]),
        tool("inspect_container", "Read what is inside a nearby chest, barrel or furnace without taking anything.", {"container": {"type": "string", "enum": ["chest", "barrel", "shulker_box", "furnace", "blast_furnace", "smoker"]}, "radius": {"type": "integer", "minimum": 1, "maximum": 16}, "dimension": dimension}, []),
        tool("locate", "Find which direction a structure or biome lies in. Answers with a direction and a rough distance, never coordinates.", {"kind": {"type": "string", "enum": ["structure", "biome"]}, "target": identifier, "dimension": dimension}, ["target"]),
        tool("smelt", "Smelt an allowed item in a nearby furnace, using carried fuel.", {"item": identifier, "count": {"type": "integer", "minimum": 1, "maximum": 64}, "fuel": identifier}, ["item"]),
        tool("sort_inventory", "Put loot away into a nearby container, keeping tools, food and fuel.", {"container": {"type": "string", "enum": ["chest", "barrel", "shulker_box"]}, "radius": {"type": "integer", "minimum": 1, "maximum": 16}, "keep": {"type": "array", "maxItems": 16, "items": identifier}, "dimension": dimension}, []),
        tool("equip", "Hold or wear an item Joi is carrying.", {"item": identifier, "destination": {"type": "string", "enum": ["hand", "off-hand", "head", "torso", "legs", "feet"]}}, ["item"]),
        tool("drop", "Throw away an allowed item Joi is carrying.", {"item": identifier, "count": {"type": "integer", "minimum": 1, "maximum": 64}}, ["item"]),
        tool("fish", "Fish for a bounded time with a carried fishing rod.", {"duration_seconds": {"type": "integer", "minimum": 1, "maximum": 300}, "dimension": dimension}, []),
        tool("sleep", "Sleep in a nearby bed.", {"radius": {"type": "integer", "minimum": 1, "maximum": 16}, "dimension": dimension}, []),
        tool("load_skill", "Read one of the notes about how to play this world before acting on a rule you are unsure of.", {"name": {"type": "string", "pattern": r"^[a-z0-9][a-z0-9_-]{0,39}$"}}, ["name"]),
        tool(
            "plan",
            "Use for a goal that needs several actions in order -- \"mine enough oak, "
            "then build a crafting table\". Core compiles the user's own words into a "
            "step plan and asks the user to confirm it. Do not call this for a single action.",
            {},
            [],
        ),
    ]


def _proposal_from_call(name: str, arguments: str, allowed_players: tuple[str, ...] = ()) -> dict[str, Any] | None:
    prefix = "minecraft_"
    if not name.startswith(prefix):
        return None
    action = name[len(prefix) :]
    try:
        parsed = json.loads(arguments)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    if action in {"follow_player", "come_to_player"} and not str(parsed.get("player") or "").strip():
        # "跟着我" names nobody. Core supplies the player the user already
        # confirmed rather than having the model invent one; with more than one
        # allowed player there is no "me" to resolve, so the model must say who.
        if len(allowed_players) != 1:
            return None
        parsed = {**parsed, "player": allowed_players[0]}
    # Identity and authority always come from Core, never model arguments.
    forbidden = {"session_id", "goal_id", "scope", "budget", "approval_id", "confirmed_scope", "permission", "receipt_id"}
    if forbidden.intersection(parsed):
        return None
    try:
        intent = canonicalize_game_intent({"final": True, "source": "voice", "intent": {"action": action, **parsed}})
    except MinecraftContractError:
        return None
    return {"final": True, "source": "voice", "intent": intent}


def _local_call_arguments(arguments: str) -> dict[str, Any] | None:
    """Parse a local proposal's arguments, refusing anything authority-shaped."""

    try:
        parsed = json.loads(arguments or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or len(parsed) > 4:
        return None
    forbidden = {"session_id", "goal_id", "scope", "budget", "approval_id", "confirmed_scope", "permission", "receipt_id", "request", "text", "command"}
    if forbidden.intersection(parsed):
        return None
    return parsed


def _motion_from_call(arguments: str) -> dict[str, Any] | None:
    """Canonicalize a motion proposal through the shared local vocabulary."""

    parsed = _local_call_arguments(arguments)
    if parsed is None:
        return None
    return character_motion_payload(parsed.get("motion"), intensity=parsed.get("intensity"))


def _skill_category(arguments: str) -> str:
    """A hint for the status line only; it never decides what runs."""

    parsed = _local_call_arguments(arguments) or {}
    category = str(parsed.get("category") or "").strip().casefold()
    return category if category in SKILL_CATEGORIES else "other"


def _safe_skill_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """The closed projection of what Core did with one local-skill proposal."""

    status = str(result.get("status") or ("started" if result.get("ok") else "rejected"))
    if status not in {"started", "rejected", "failed"}:
        status = "failed"
    if not result.get("ok"):
        status = "rejected" if status == "started" else status
    safe: dict[str, Any] = {"status": status}
    skill = str(result.get("skill") or "").strip().casefold()
    if _SKILL_LABEL.fullmatch(skill):
        safe["skill"] = skill
    if status == "started":
        safe["requires_confirmation"] = bool(result.get("requires_confirmation"))
    error = str(result.get("error") or "")
    if error in SAFE_SKILL_ERRORS:
        safe["error"] = error
    elif status != "started":
        safe["error"] = "realtime_skill_unavailable"
    return safe


def _safe_action_result(action: str, result: Mapping[str, Any]) -> dict[str, Any]:
    status = str(result.get("status") or "failed")
    if status not in {"completed", "partial", "unverified", "failed", "cancelled"}:
        status = "failed"
    safe: dict[str, Any] = {
        "action": action if action in {tool["function"]["name"][10:] for tool in minecraft_proposal_tools()} else "unknown",
        "status": status,
        "summary": "completed" if status == "completed" else "not_completed",
        "recovery_required": bool(result.get("recovery_required")),
    }
    # Only read-only actions may carry text back to the provider, and only the
    # bounded sanitized projection Core rendered for them.
    if action in {"observe_screen", "inspect_container", "lookup_recipe", "locate", "load_skill"} and status == "completed":
        observation = _bounded_text(result.get("observation"), 1_200)
        if observation:
            safe["observation"] = observation
    return safe


def _provider_url(config: RealtimeVoiceConfig) -> str:
    if not is_safe_qwen_realtime_url(config.url) or config.model not in QWEN_REALTIME_MODELS:
        raise ValueError("realtime_config_error")
    return f"{config.url}?{urlencode({'model': config.model})}"


def _provider_message(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw.encode("utf-8", errors="ignore")) > MAX_PROVIDER_MESSAGE_BYTES:
        raise ValueError("realtime_invalid_response")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("realtime_invalid_response")
    return parsed


def _provider_start_error(event: Mapping[str, Any]) -> str:
    return "realtime_auth_failed" if str(event.get("type") or "") == "error" else "realtime_invalid_response"


def _connection_error(exc: Exception) -> str:
    name = type(exc).__name__.casefold()
    if "timeout" in name:
        return "realtime_timeout"
    if "status" in name or "handshake" in name:
        return "realtime_auth_failed"
    return "realtime_unavailable"


def _bounded_transcript(value: Any) -> str:
    return _bounded_text(value, MAX_TRANSCRIPT_CHARS)


def _bounded_assistant_text(value: Any) -> str:
    return _bounded_text(value, 2_000)


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "")
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    return text[:limit]


def _private_response_key(response: Mapping[str, Any]) -> str:
    return _private_response_id_key(response.get("id"))


def _private_response_id_key(value: Any) -> str:
    value = str(value or "")[:200]
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def _private_item_key(value: Any) -> str:
    value = str(value or "")[:200]
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def _safe_public_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    event_type = str(payload.get("type") or "")
    if event_type == "state":
        state = str(payload.get("state") or "")
        if state not in {"idle", "connecting", "listening", "user_speaking", "thinking", "assistant_speaking", "acting", "paused", "recovery_required", "error"}:
            return {}
        result: dict[str, Any] = {"type": "state", "state": state}
        if isinstance(payload.get("epoch"), int):
            result["epoch"] = max(0, int(payload["epoch"]))
        if payload.get("reason") in {"user_stop", "transport_lost", "shutdown", "guest_time_limit"}:
            result["reason"] = payload["reason"]
        return result
    if event_type == "barge_in":
        return {"type": "barge_in", "epoch": max(0, int(payload.get("epoch") or 0))}
    if event_type in {"user_transcript", "assistant_transcript"}:
        text = _bounded_transcript(payload.get("text"))
        if not text:
            return {}
        result = {"type": event_type, "text": text, "final": bool(payload.get("final"))}
        if isinstance(payload.get("epoch"), int):
            result["epoch"] = max(0, int(payload["epoch"]))
        return result
    if event_type == "assistant_text":
        text = _bounded_assistant_text(payload.get("text"))
        return {"type": event_type, "text": text, "epoch": max(0, int(payload.get("epoch") or 0)), "output": "local_tts"} if text else {}
    if event_type == "character_motion":
        # Re-canonicalized on the way out, so the Shell can only ever receive a
        # name from the closed local table and bounded playback numbers.
        motion = payload.get("motion") if isinstance(payload.get("motion"), Mapping) else {}
        safe_motion = character_motion_payload(
            motion.get("name"),
            duration_ms=motion.get("duration_ms"),
            loop=motion.get("loop"),
            intensity=motion.get("intensity"),
        )
        if safe_motion is None:
            return {}
        return {"type": "character_motion", "motion": safe_motion, "epoch": max(0, int(payload.get("epoch") or 0))}
    if event_type == "skill_action":
        status = str(payload.get("status") or "")
        result = {"type": "skill_action", "status": status if status in {"started", "rejected", "failed"} else "failed"}
        skill = str(payload.get("skill") or "").strip().casefold()
        if _SKILL_LABEL.fullmatch(skill):
            result["skill"] = skill
        if payload.get("error") in SAFE_SKILL_ERRORS or payload.get("error") in {"realtime_action_ambiguous", "realtime_action_invalid"}:
            result["error"] = payload["error"]
        if "requires_confirmation" in payload:
            result["requires_confirmation"] = bool(payload.get("requires_confirmation"))
        return result
    if event_type == "game_action":
        action = str(payload.get("action") or "")
        status = str(payload.get("status") or "")
        result = {"type": "game_action", "status": status if status in {"acting", "completed", "partial", "unverified", "failed", "cancelled", "rejected"} else "failed"}
        if action:
            result["action"] = action[:40]
        if payload.get("error") in {"realtime_action_ambiguous", "realtime_action_invalid", "attack_requires_explicit_instruction"}:
            result["error"] = payload["error"]
        if "recovery_required" in payload:
            result["recovery_required"] = bool(payload.get("recovery_required"))
        return result
    if event_type == "latency":
        # Whole milliseconds and a turn count. There is nothing else in it, and
        # nothing else may be added: this is a debug projection of a private
        # call, so a field that is not a bounded number does not belong.
        result = {"type": "latency"}
        for field_name in ("epoch", "first_text_ms", "answer_ms", "voice_ms", "total_ms", "turns", "p50_ms", "p95_ms"):
            value = payload.get(field_name)
            if isinstance(value, int) and not isinstance(value, bool):
                result[field_name] = max(0, min(int(value), 600000))
        return result if len(result) > 1 else {}
    if event_type == "error":
        error = str(payload.get("error") or "")
        allowed = {"realtime_provider_error", "realtime_disconnected", "realtime_audio_overflow", "realtime_call_conflict"}
        return {"type": "error", "error": error if error in allowed else "realtime_unavailable"}
    return {}


def _safe_stop_reason(reason: str) -> str:
    return reason if reason in {"user_stop", "transport_lost", "shutdown", "guest_time_limit"} else "user_stop"


def _close_socket(socket: RealtimeSocket | None) -> None:
    if socket is None:
        return
    try:
        socket.close()
    except Exception:
        return


def _caption_language_label(chat_locale: str) -> str:
    return "用户本轮说话所用的语言" if _follows_user(chat_locale) else voice_language_label(chat_locale)


def _format_lead(voice_locale: str, chat_locale: str) -> str:
    """The two-line contract, stated before anything else can bury it."""

    if not _splits_channels(voice_locale, chat_locale):
        return ""
    spoken = voice_language_label(voice_locale)
    return (
        f"输出格式（最高优先级）：每一轮都只输出两行，第一行以「朗读：」开头并用{spoken}，"
        f"第二行以「字幕：」开头并用{_caption_language_label(chat_locale)}；两行是同一句话的两种语言。\n"
    )


def _language_rule(voice_locale: str, chat_locale: str) -> str:
    """Realtime's one text channel has to serve both the ear and the screen.

    The normal chat path writes the spoken line separately from the displayed
    one, so a Japanese voice can answer a Chinese message in Chinese on screen.
    Here the provider's text goes straight to the character voice, and a Chinese
    answer read by a Japanese voice comes out as kanji readings rather than
    words. So the provider is told which language is spoken -- and, when the
    caption is meant to be a different language, asked for both lines at once.
    """

    if not str(voice_locale or "").strip():
        return ""
    spoken = voice_language_label(voice_locale)
    if not _splits_channels(voice_locale, chat_locale):
        return (
            f"你的每一句回答都会被角色的{spoken}声音直接朗读，因此必须完整使用{spoken}书写。"
            f"即使用户用别的语言说话，也不要切换朗读语言，也不要混用两种语言；只在{spoken}里自然表达。"
        )
    caption = _caption_language_label(chat_locale)
    return (
        f"再说一次输出格式，这是硬性要求：你的回答会被角色的{spoken}声音朗读，而字幕要用{caption}显示，"
        "所以每一轮都必须输出下面两行，不要有第三行，也不要有解释，即使只是打个招呼也要两行：\n"
        f"朗读：<用{spoken}写的那句话>\n"
        f"字幕：<同一句话，用{caption}写>\n"
        f"两行必须是同一句话的两种语言；朗读行只能是{spoken}，不要混用语言。"
    )


def _conversation_instructions(voice_locale: str = "", chat_locale: str = "", persona: str = "") -> str:
    return "".join(
        [
            _format_lead(voice_locale, chat_locale),
            "你是 Joi，正在与用户进行低延迟语音对话。回答简洁、自然、友好。",
            _persona_block(persona),
            "输出必须是适合直接朗读的纯文本；不要说模型、供应商、路径、标识符、日志、JSON、命令或秘密。",
            _local_skill_block(),
            _language_rule(voice_locale, chat_locale),
        ]
    )


def _local_skill_block() -> str:
    """How the two local proposals are described to the provider.

    Written as "propose, don't perform": the provider decides that this turn
    wants Joi's body or Joi's skills, and Core decides what that means. Saying
    so plainly here is what stops a model from narrating a finished action it
    never took.
    """

    return (
        "你有两个本地提案工具，只在用户确实提出请求时调用，一轮最多调用一个，纯聊天绝不调用：\n"
        "joi_play_motion：用户想看你做动作时调用（打招呼、说话动作、开心庆祝、手指枪、跳舞、回到待机），"
        "只能从枚举里选一个名字；这只是本机角色动画。\n"
        "joi_run_skill：用户要在这台电脑上做事时调用（打开应用、点击输入、上网搜索、看当前屏幕或视频、写代码改代码）。"
        "你不需要也不能转述请求内容：Joi Core 会用用户这一轮自己说的原话重新规划。\n"
        "工具只是提案：Core 独立判断风险，需要授权的动作会在界面上等用户确认。\n"
        "拿到工具结果后：status 是 started 就说你已经着手，并在 requires_confirmation 为真时提醒用户在界面上点确认；"
        "status 是 rejected 或 failed 就直说这次没能开始，可以请用户换个说法或改用文字输入；"
        "任何情况下都不要声称动作已经完成，也不要念出工具名、参数、状态码或标识符。\n"
    )


def _minecraft_instructions(
    voice_locale: str = "",
    chat_locale: str = "",
    persona: str = "",
    world_memory: str = "",
    allowed_players: tuple[str, ...] = (),
    skill_index: str = "",
) -> str:
    return "".join(
        [
            _format_lead(voice_locale, chat_locale),
            "你是 Joi，正在和用户一起玩 Minecraft。简短自然地对话。",
            _persona_block(persona),
            _world_memory_block(world_memory),
            _allowed_players_block(allowed_players),
            _bounded_text(skill_index, 1_200),
            _local_skill_block(),
            "你可以在确有必要时调用一个 minecraft_* 工具提出单个游戏动作；工具只是提案，Core 会独立检查权限、范围和预算。",
            "每一轮最多提出一个动作，不要猜测坐标、权限、会话标识或完成结果。",
            "战斗规则：除非用户在同一轮里明确要求攻击，否则永远不要提出 minecraft_attack；害怕或躲避时可以提出 minecraft_flee 或 minecraft_guard。任何动作都不得以玩家为目标。",
            "收到工具结果后才可以描述是否完成；输出必须是适合直接朗读的纯文本。",
            _language_rule(voice_locale, chat_locale),
        ]
    )


def _allowed_players_block(allowed_players: tuple[str, ...]) -> str:
    """Who "me" is, so following the user is not a guess.

    The model was never told any player name, so "跟着我" produced an invented
    one and Core refused it as out of scope. With exactly one confirmed player
    the name can be left out entirely and Core fills it in.
    """

    names = [str(name) for name in allowed_players if str(name).strip()]
    if not names:
        return "当前没有任何被授权跟随的玩家；用户要你跟随时，请告诉他先在游戏设置里填写自己的 Minecraft 玩家名。\n"
    if len(names) == 1:
        return f"用户的 Minecraft 玩家名是 {names[0]}。要跟随或靠近用户时，直接调用 minecraft_follow_player / minecraft_come_to_player 即可，可以不填 player。\n"
    return f"被授权的玩家：{'、'.join(names[:8])}。跟随或靠近时必须在 player 里写出其中一个名字。\n"


def _world_memory_block(world_memory: str) -> str:
    text = _bounded_text(world_memory, 500).strip()
    if not text:
        return ""
    return f"世界记忆（可参考，不要编造未给出的细节）：\n{text}\n"


_ATTACK_INSTRUCTION_TOKENS = (
    "攻击",
    "打它",
    "打他",
    "打她",
    "打怪",
    "打死",
    "打那",
    "开打",
    "揍",
    "attack",
    "kill",
    "fight",
)


def _transcript_authorizes_attack(text: str) -> bool:
    """Scheme A gate: only an explicit attack instruction in the same turn counts.

    Conservative by design: false negatives merely refuse an attack, while the
    token list avoids generic words like a bare "打" so that "打包/打扫" can
    never arm the most dangerous primitive.
    """

    lowered = str(text or "").casefold().strip()
    return bool(lowered) and any(token in lowered for token in _ATTACK_INSTRUCTION_TOKENS)


def _persona_block(persona: str) -> str:
    """The character harness, sanitized and bounded for the cloud instructions.

    Persona text is session-invariant, so it belongs in instructions (which
    ``session.update`` sends exactly once at start); per-turn context must go
    through ``conversation.item.create`` instead.
    """

    text = _bounded_text(persona, 1_200).strip()
    if not text:
        return ""
    return f"角色设定（始终遵守）：\n{text}\n"


def _follows_user(chat_locale: str) -> bool:
    return str(chat_locale or "").strip().casefold() in {"", CHAT_LANGUAGE_FOLLOW, "auto"}


def _base_language(locale: str) -> str:
    """The bare language behind a locale, with the aliases the voices use."""

    base = str(locale or "").strip().replace("_", "-").casefold().split("-")[0]
    return {"yue": "zh", "cmn": "zh", "jpn": "ja", "kor": "ko", "eng": "en"}.get(base, base)


def _splits_channels(voice_locale: str, chat_locale: str) -> bool:
    """Two lines are only worth their tokens when the two channels can differ."""

    voice = str(voice_locale or "").strip().casefold().split("-")[0]
    if not voice:
        return False
    if _follows_user(chat_locale):
        return True
    return str(chat_locale or "").strip().casefold().split("-")[0] != voice


def _split_spoken_and_caption(text: str) -> tuple[str, str]:
    """Read the two tagged parts, or return the whole answer with no caption.

    A speech model does not reliably produce a two-line format, so this reports
    what it actually found: an empty caption means "the model gave one line",
    and the caller decides what to show rather than captioning the spoken
    language as if it were the requested one.

    The caption tag is also honoured mid-line. A model that answers
    "朗读：X 字幕：Y" on one line used to have the whole string both spoken and
    displayed, which made Joi read the word "字幕" out loud.
    """

    raw = str(text or "")
    inline = _CAPTION_TAG_INLINE.search(raw)
    if inline and "\n" not in raw[: inline.start()]:
        spoken_part = raw[: inline.start()]
        caption_part = raw[inline.end():]
        return _strip_tags(spoken_part), _strip_tags(caption_part)
    spoken: list[str] = []
    caption: list[str] = []
    current: list[str] | None = None
    for line in raw.splitlines():
        if _SPOKEN_TAG.match(line):
            current = spoken
            line = _SPOKEN_TAG.sub("", line, count=1)
        elif _CAPTION_TAG.match(line):
            current = caption
            line = _CAPTION_TAG.sub("", line, count=1)
        if current is None:
            continue
        current.append(line)
    spoken_text = "\n".join(spoken).strip()
    caption_text = "\n".join(caption).strip()
    if spoken_text and caption_text:
        return spoken_text, caption_text
    return _strip_tags(raw), ""


def _strip_tags(value: str) -> str:
    return "\n".join(
        _CAPTION_TAG.sub("", _SPOKEN_TAG.sub("", row, count=1), count=1) for row in str(value or "").splitlines()
    ).strip()
