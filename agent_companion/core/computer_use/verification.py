from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_companion.core.computer_use.schemas import ComputerObservation


@dataclass(frozen=True)
class VerificationSignals:
    screenshot_changed: bool | None = None
    title_changed: bool | None = None
    ocr_changed: bool | None = None
    artifact_changed: bool | None = None
    dimensions_changed: bool | None = None

    def to_agent_state(self) -> dict[str, bool | None]:
        return {
            "screenshot_changed": self.screenshot_changed,
            "title_changed": self.title_changed,
            "ocr_changed": self.ocr_changed,
            "artifact_changed": self.artifact_changed,
            "dimensions_changed": self.dimensions_changed,
        }

    def has_strong_positive_signal(self) -> bool:
        return any(
            value is True
            for value in (
                self.screenshot_changed,
                self.title_changed,
                self.ocr_changed,
                self.dimensions_changed,
            )
        )

    def has_known_signal(self) -> bool:
        return any(value is not None for value in self.to_agent_state().values())


@dataclass(frozen=True)
class PostActionVerification:
    status: str
    summary: str
    signals: VerificationSignals = field(default_factory=VerificationSignals)
    artifacts: list[str] = field(default_factory=list)

    def to_agent_state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "signals": self.signals.to_agent_state(),
            "artifacts": list(self.artifacts),
        }


def verify_post_action(before: ComputerObservation | None, after: ComputerObservation | None) -> PostActionVerification:
    artifacts = _verification_artifacts(before, after)
    if before is None or after is None:
        return PostActionVerification(
            "unavailable",
            "操作已执行，暂时无法判断画面变化。",
            VerificationSignals(),
            artifacts,
        )

    signals = VerificationSignals(
        screenshot_changed=None,
        title_changed=_changed(before.title, after.title),
        ocr_changed=_changed(_ocr_signature(before), _ocr_signature(after)),
        artifact_changed=_changed(before.screenshot_rel, after.screenshot_rel),
        dimensions_changed=(before.width, before.height) != (after.width, after.height),
    )
    if signals.has_strong_positive_signal():
        status = "changed"
        summary = "操作后画面有变化。"
    elif signals.has_known_signal():
        status = "likely_noop"
        summary = "操作已执行，但画面变化不明显。"
    else:
        status = "unavailable"
        summary = "操作已执行，暂时无法判断画面变化。"
    return PostActionVerification(status, summary, signals, artifacts)


def _verification_artifacts(before: ComputerObservation | None, after: ComputerObservation | None) -> list[str]:
    rows: list[str] = []
    for observation in (before, after):
        if observation and observation.screenshot_rel and observation.screenshot_rel not in rows:
            rows.append(observation.screenshot_rel)
    return rows


def _changed(before: str, after: str) -> bool | None:
    left = (before or "").strip()
    right = (after or "").strip()
    if not left or not right:
        return None
    return left != right


def _ocr_signature(observation: ComputerObservation) -> str:
    blocks = observation.ocr.get("text_blocks") if isinstance(observation.ocr, dict) else []
    if not isinstance(blocks, list):
        return ""
    snippets: list[str] = []
    for block in blocks[:12]:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "").strip()
        if text:
            snippets.append(text[:160])
    return "\n".join(snippets)
