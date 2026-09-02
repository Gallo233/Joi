"""Offline speech through the voices macOS already has.

GPT-SoVITS gives a character its own voice, but it is a separate service the
user has to install, configure and keep running. Until that exists there is no
audio at all, which makes the whole voice path -- generation epochs, barge-in,
stopping when the character changes -- impossible to exercise.

This is the floor underneath it: `say`, which is present on every Mac, needs no
key, and never sends a word off the machine. It is not the character's voice
and is not meant to be; it is what lets the character speak at all.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid


SAY = Path("/usr/bin/say")

# `say` takes the text as an argument. Anything shell-like is already excluded
# by safe_voice_line, but the runner passes a list rather than a shell string,
# so this only guards against a single absurd input reaching a subprocess.
MAX_SPOKEN_CHARS = 600

# `say` reads `[[...]]` in its input as commands to the synthesiser, not as
# words. Joi's own prosody is prepended as exactly such commands, so any pair
# surviving in the text would be a second, unaccountable author of them --
# stripped rather than escaped, because there is no escape for this syntax.
EMBEDDED_COMMAND = re.compile(r"\[\[|\]\]")


@dataclass(frozen=True)
class Prosody:
    """How far from the voice's own delivery to push, each field -1 to 1.

    Relative rather than absolute on purpose. The voice for a given language is
    whatever that Mac has installed, its natural pitch and pace are its own,
    and setting absolutes would flatten every voice onto one delivery.
    """

    pitch: float = 0.0
    rate: float = 0.0
    volume: float = 0.0
    # How much the pitch moves within a sentence: the difference between
    # animated and monotone, which is most of what reads as mood.
    modulation: float = 0.0


# What each of Joi's emotions sounds like on a voice that cannot act.
#
# A system voice has one timbre and no performance, so mood has to come out of
# pace, pitch and range alone. The moves are deliberately modest: overdriving
# these lands on a cartoon rather than on a character, and this voice's job is
# to be unobtrusive until a real one is configured.
EMOTION_PROSODY: dict[str, Prosody] = {
    "neutral": Prosody(),
    "happy": Prosody(pitch=0.45, rate=0.25, volume=0.15, modulation=0.5),
    "thinking": Prosody(pitch=-0.15, rate=-0.3, volume=-0.1, modulation=-0.3),
    "serious": Prosody(pitch=-0.35, rate=-0.05, modulation=-0.5),
    "alert": Prosody(pitch=0.3, rate=0.4, volume=0.3, modulation=0.3),
    "worried": Prosody(pitch=-0.25, rate=-0.25, volume=-0.15, modulation=-0.2),
}

# How far a full -1..1 swing moves each of `say`'s own controls. `pbas` and
# `pmod` are on its 0-127 pitch scale, `rate` is words per minute, and `volm`
# runs 0 to 1.
PITCH_RANGE = 10.0
RATE_RANGE = 40.0
VOLUME_RANGE = 0.25
MODULATION_RANGE = 3.0

# Written as a WAVE file because that is what every browser will play back;
# `say` defaults to AIFF, which Chromium-based shells will not decode.
DATA_FORMAT = "LEI16@22050"


@dataclass(frozen=True)
class SystemVoice:
    name: str
    language: str


def available() -> bool:
    return sys.platform == "darwin" and SAY.is_file()


def voices() -> list[SystemVoice]:
    """Installed voices, or nothing when this platform has no `say`."""

    if not available():
        return []
    try:
        completed = subprocess.run([str(SAY), "-v", "?"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    found: list[SystemVoice] = []
    for line in completed.stdout.splitlines():
        match = re.match(r"^(.+?)\s{2,}([a-z]{2}(?:_[A-Z]{2})?)\s", line)
        if match:
            found.append(SystemVoice(match.group(1).strip(), match.group(2)))
    return found


def preferred_voice(language: str) -> str:
    """The first installed voice for a language, or the system default.

    A character speaking Chinese through an English voice is worse than no
    voice at all, so the language the character declares picks the voice.
    """

    wanted = str(language or "").strip().replace("-", "_").casefold()
    if not wanted:
        return ""
    installed = voices()
    for voice in installed:
        if voice.language.casefold() == wanted:
            return voice.name
    # `zh` should still find `zh_CN`.
    prefix = wanted.split("_")[0]
    for voice in installed:
        if voice.language.casefold().startswith(prefix):
            return voice.name
    return ""


def synthesize(
    text: str,
    *,
    output_dir: Path,
    language: str = "",
    rate: float = 1.0,
    emotion: str = "neutral",
    pitch: float | None = None,
) -> Path | None:
    """Speak `text` into a WAV file, or return nothing if it cannot.

    `emotion` is the mood the line was written in, and `pitch` overrides that
    mood's default when the character package declared one -- the author's own
    number about their own character beats the generic table.

    Failure is silent by design: the caller keeps showing the text and simply
    plays no audio, which is the same degradation the GPT-SoVITS path uses.
    """

    spoken = EMBEDDED_COMMAND.sub("", " ".join(str(text or "").split()))[:MAX_SPOKEN_CHARS]
    if not spoken or not available():
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"say-{uuid.uuid4().hex[:12]}.wav"
    command = [str(SAY), "-o", str(destination), "--data-format", DATA_FORMAT]
    voice = preferred_voice(language)
    if voice:
        command += ["-v", voice]
    words_per_minute = _rate_to_wpm(rate)
    if words_per_minute:
        command += ["-r", str(words_per_minute)]
    # Prepended, so the settings are in force before the first word rather than
    # arriving partway through the line.
    command.append(prosody_prefix(emotion, pitch) + spoken)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        return None
    return destination


def prosody_prefix(emotion: str, pitch: float | None = None) -> str:
    """`say` commands that put the voice into one mood, or nothing for neutral.

    Every value is signed and relative, so the voice keeps its own baseline and
    a neutral line produces no commands at all -- byte-for-byte what this
    module generated before it knew about emotion.
    """

    from agent_companion.core.voice import normalize_emotion

    prosody = EMOTION_PROSODY.get(normalize_emotion(emotion), EMOTION_PROSODY["neutral"])
    if pitch is not None:
        try:
            prosody = replace(prosody, pitch=min(1.0, max(-1.0, float(pitch))))
        except (TypeError, ValueError):
            pass
    parts = [
        _command("pbas", prosody.pitch * PITCH_RANGE, 1),
        _command("pmod", prosody.modulation * MODULATION_RANGE, 1),
        _command("rate", prosody.rate * RATE_RANGE, 0),
        _command("volm", prosody.volume * VOLUME_RANGE, 2),
    ]
    return "".join(part for part in parts if part)


def _command(name: str, amount: float, decimals: int) -> str:
    """One relative `say` command, or nothing when the move is imperceptible."""

    rounded = round(amount, decimals)
    if not rounded:
        return ""
    value = f"{rounded:+.{decimals}f}" if decimals else f"{rounded:+.0f}"
    return f"[[{name} {value}]]"


def _rate_to_wpm(rate: float) -> int:
    """Map the character's speech-speed multiplier onto `say`'s words/minute."""

    try:
        multiplier = float(rate)
    except (TypeError, ValueError):
        return 0
    if not 0.1 <= multiplier <= 4.0 or abs(multiplier - 1.0) < 0.02:
        return 0
    # 175 wpm is `say`'s own default.
    return max(90, min(400, round(175 * multiplier)))


def temporary_output_dir(workspace: Path) -> Path:
    root = workspace / "data" / "agent_companion" / "voice"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError:
        return Path(tempfile.gettempdir())
