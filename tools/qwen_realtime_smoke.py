#!/usr/bin/env python3
"""Run a privacy-preserving Qwen Realtime text-response smoke test."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
import sys
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_companion.core.config import load_app_config
from agent_companion.core.realtime_voice import QwenRealtimeSession


FRAME_BYTES = 1_280  # 40 ms, PCM16LE mono at 16 kHz.


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the Core-owned Qwen Realtime path.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--pcm", type=Path, required=True, help="Raw 16 kHz mono PCM16LE speech.")
    args = parser.parse_args()
    config = load_app_config(args.workspace.resolve() / "config.yaml").realtime_voice
    try:
        audio = args.pcm.read_bytes()
    except OSError:
        print("qwen_realtime_smoke_failed audio_unavailable")
        return 1
    if not config.is_configured or not audio or len(audio) > 10 * 1024 * 1024:
        print("qwen_realtime_smoke_failed setup_required")
        return 1

    observed = {"user_final": False, "assistant_final": False, "assistant_text": False, "error": False}

    def emit(event: dict[str, object]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "user_transcript" and event.get("final") is True:
            observed["user_final"] = True
        elif event_type == "assistant_transcript" and event.get("final") is True:
            observed["assistant_final"] = True
        elif event_type == "assistant_text":
            observed["assistant_text"] = True
        elif event_type == "error":
            observed["error"] = True

    session = QwenRealtimeSession(
        config,
        session_id="realtime-0000000000000000",
        owner_id="smoke-owner",
        mode="conversation",
        minecraft_session_id="",
        emit=emit,
        execute_action=lambda *_: {"ok": False},
        cancel_action=lambda *_: {"ok": True},
    )
    started = session.start()
    if not started.get("ok"):
        print(f"qwen_realtime_smoke_failed {started.get('error') or 'start_failed'}")
        return 1
    sequence = 1
    frames = [b"\0" * FRAME_BYTES for _ in range(10)]
    frames.extend(audio[index : index + FRAME_BYTES] for index in range(0, len(audio), FRAME_BYTES))
    frames.extend(b"\0" * FRAME_BYTES for _ in range(25))
    try:
        for frame in frames:
            padded = frame.ljust(FRAME_BYTES, b"\0")
            result = session.append_audio(
                sequence,
                base64.b64encode(padded).decode("ascii"),
                sample_rate=16_000,
                channels=1,
                sample_width=2,
            )
            if not result.get("ok"):
                print(f"qwen_realtime_smoke_failed {result.get('error') or 'audio_rejected'}")
                return 1
            sequence += 1
            time.sleep(0.04)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and not observed["assistant_text"] and not observed["error"]:
            time.sleep(0.05)
    finally:
        session.stop()
    ok = observed["user_final"] and observed["assistant_final"] and observed["assistant_text"] and not observed["error"]
    print(
        "qwen_realtime_smoke_{} user_final={} assistant_final={} local_tts_text={} provider_error={}".format(
            "ok" if ok else "failed",
            str(observed["user_final"]).lower(),
            str(observed["assistant_final"]).lower(),
            str(observed["assistant_text"]).lower(),
            str(observed["error"]).lower(),
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
