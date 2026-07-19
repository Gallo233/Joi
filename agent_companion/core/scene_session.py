from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import time
from typing import Iterable


SCENE_MODES = {"quiet", "commentary", "translate", "analysis", "accessibility"}
SPOILER_LEVELS = {"none", "current_scene", "full"}


@dataclass(frozen=True)
class SceneObservation:
    created_at: float
    visual_summary: str = ""
    transcript: tuple[str, ...] = ()
    transcript_source: str = ""
    visual_changed: bool = False
    transcript_changed: bool = False
    event_kinds: tuple[str, ...] = ()
    should_comment: bool = False
    reason: str = ""
    raw_media_retained: bool = False

    def payload(self) -> dict:
        return asdict(self)


@dataclass
class SceneSession:
    mode: str = "quiet"
    spoiler_level: str = "none"
    min_comment_interval_seconds: float = 30.0
    last_visual_key: str = ""
    last_transcript_key: str = ""
    last_comment_at: float = 0.0
    observations: int = 0
    chapters: list[dict] = field(default_factory=list)

    def configure(self, *, mode: str, spoiler_level: str, min_comment_interval_seconds: float) -> None:
        self.mode = mode if mode in SCENE_MODES else "quiet"
        self.spoiler_level = spoiler_level if spoiler_level in SPOILER_LEVELS else "none"
        self.min_comment_interval_seconds = max(8.0, min(float(min_comment_interval_seconds or 30), 300.0))

    def reset(self, *, mode: str = "quiet", spoiler_level: str = "none", min_comment_interval_seconds: float = 30.0) -> None:
        self.mode = mode if mode in SCENE_MODES else "quiet"
        self.spoiler_level = spoiler_level if spoiler_level in SPOILER_LEVELS else "none"
        self.min_comment_interval_seconds = max(8.0, min(float(min_comment_interval_seconds or 30), 300.0))
        self.last_visual_key = ""
        self.last_transcript_key = ""
        self.last_comment_at = 0.0
        self.observations = 0
        self.chapters.clear()

    def observe(
        self,
        visual_summary: str,
        transcript: Iterable[str],
        *,
        transcript_source: str = "",
        now: float | None = None,
        proactive_enabled: bool = False,
    ) -> SceneObservation:
        now = time.time() if now is None else float(now)
        visual = " ".join(str(visual_summary or "").split())[:800]
        rows = tuple(" ".join(str(row or "").split())[:220] for row in transcript if str(row or "").strip())[-12:]
        visual_key = _key(visual)
        transcript_key = _key("\n".join(rows))
        visual_changed = bool(visual_key and self.last_visual_key and visual_key != self.last_visual_key)
        transcript_changed = bool(transcript_key and transcript_key != self.last_transcript_key)
        event_kinds: list[str] = []
        if visual_changed:
            event_kinds.append("scene_change")
        if transcript_changed:
            event_kinds.append("new_dialogue")
        if transcript_changed and _chapter_boundary(rows):
            event_kinds.append("chapter_boundary")
        if transcript_changed and _high_salience(rows):
            event_kinds.append("high_salience")
        cooled_down = now - self.last_comment_at >= self.min_comment_interval_seconds
        should_comment = bool(
            proactive_enabled
            and self.mode != "quiet"
            and cooled_down
            and (
                (self.mode in {"translate", "analysis"} and transcript_changed)
                or (self.mode == "accessibility" and (visual_changed or transcript_changed))
                or (self.mode == "commentary" and ("high_salience" in event_kinds or "chapter_boundary" in event_kinds or visual_changed))
            )
        )
        reason = "+".join(event_kinds) if should_comment else "quiet_or_no_meaningful_change"
        if visual_key:
            self.last_visual_key = visual_key
        if transcript_key:
            self.last_transcript_key = transcript_key
        self.observations += 1
        if "chapter_boundary" in event_kinds:
            self.chapters.append({"created_at": now, "summary": visual or (rows[-1] if rows else "章节变化")})
            self.chapters = self.chapters[-24:]
        if should_comment:
            self.last_comment_at = now
        return SceneObservation(
            created_at=now,
            visual_summary=visual,
            transcript=rows,
            transcript_source=transcript_source,
            visual_changed=visual_changed,
            transcript_changed=transcript_changed,
            event_kinds=tuple(event_kinds),
            should_comment=should_comment,
            reason=reason,
            raw_media_retained=False,
        )

    def payload(self) -> dict:
        return {
            "mode": self.mode,
            "spoiler_level": self.spoiler_level,
            "raw_media_retention": False,
            "observations": self.observations,
            "chapters": list(self.chapters),
        }


def _key(value: str) -> str:
    normalized = "".join(str(value or "").casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _chapter_boundary(rows: tuple[str, ...]) -> bool:
    text = " ".join(rows[-2:]).casefold()
    return any(token in text for token in ("下一集", "第章", "chapter", "episode", "几天后", "与此同时", "previously"))


def _high_salience(rows: tuple[str, ...]) -> bool:
    text = " ".join(rows[-3:]).casefold()
    return any(token in text for token in ("但是", "原来", "真相", "危险", "成功", "失败", "警告", "注意", "为什么", "竟然", "不可能", "？", "!", "！"))
