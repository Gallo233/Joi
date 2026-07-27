"""What Joi knows about a target, and when that knowledge stops being true.

A coordinate is not a target. It is something derived from a target that was
identified on a particular display, in a particular window, in a particular
screenshot, at a particular moment. Any of those can change between observing
and acting: the window moves, the user switches Spaces, a display is unplugged,
the app repaints. Once they do, the coordinate points at whatever now occupies
that spot.

So evidence carries the identity it was gathered under and is checked again
immediately before acting. A mismatch means observe again -- never "click
anyway and see" (TDD §9.1, §9.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any

from agent_companion.core.vision.capture_geometry import CaptureGeometry


# Ordered weakest-to-strongest; the pipeline must prefer the strongest source
# that produced a usable candidate (TDD §9.1).
PERCEPTION_SOURCES = ("coordinate", "vision", "ocr", "accessibility", "application")

# Vision alone locates something that looks right; it does not establish that
# the thing is the control the user meant.
VISION_ONLY_SOURCES = frozenset({"vision", "ocr", "coordinate"})

DEFAULT_EVIDENCE_TTL_SECONDS = 20.0
MIN_AUTONOMOUS_CONFIDENCE = 0.75


@dataclass(frozen=True)
class CaptureIdentity:
    """The conditions an observation was made under."""

    display_layout_digest: str = ""
    display_id: str = ""
    window_id: str = ""
    app_id: str = ""
    scale: float = 1.0
    capture_digest: str = ""
    geometry_trusted: bool = True

    def payload(self) -> dict[str, Any]:
        return {
            "display_layout_digest": self.display_layout_digest,
            "display_id": self.display_id,
            "window_id": self.window_id,
            "app_id": self.app_id,
            "scale": round(float(self.scale), 4),
            "capture_digest": self.capture_digest,
            "geometry_trusted": self.geometry_trusted,
        }

    def differences(self, other: "CaptureIdentity") -> tuple[str, ...]:
        """Name every field that moved, so the reason is reportable."""
        changed: list[str] = []
        for name in ("display_layout_digest", "display_id", "window_id", "app_id", "capture_digest"):
            mine, theirs = getattr(self, name), getattr(other, name)
            # An empty value means "not observed", which cannot prove a change.
            if mine and theirs and mine != theirs:
                changed.append(name)
        if abs(float(self.scale) - float(other.scale)) > 1e-6:
            changed.append("scale")
        return tuple(changed)


@dataclass(frozen=True)
class TargetEvidence:
    """A resolved target, bound to the conditions that produced it."""

    target_id: str
    label: str
    source: str
    confidence: float
    identity: CaptureIdentity
    role: str = ""
    ambiguity: str = "none"
    clickable: bool | None = None
    enabled: bool | None = None
    capture_scope: str = "window"
    logical_bounds: tuple[int, int, int, int] | None = None
    pixel_bounds: tuple[int, int, int, int] | None = None
    domain: str = ""
    path: str = ""
    observed_at: float = field(default_factory=time.time)
    ttl_seconds: float = DEFAULT_EVIDENCE_TTL_SECONDS
    alternatives: int = 0

    @property
    def expires_at(self) -> float:
        return self.observed_at + max(1.0, float(self.ttl_seconds))

    def expired(self, now: float | None = None) -> bool:
        return (time.time() if now is None else float(now)) >= self.expires_at

    @property
    def evidence_digest(self) -> str:
        payload = {
            "target_id": self.target_id,
            "label": self.label,
            "source": self.source,
            "logical_bounds": list(self.logical_bounds or ()),
            "identity": self.identity.payload(),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]

    @property
    def logical_center(self) -> tuple[int, int] | None:
        if not self.logical_bounds:
            return None
        left, top, right, bottom = self.logical_bounds
        return ((left + right) // 2, (top + bottom) // 2)

    def payload(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "label": self.label,
            "role": self.role,
            "source": self.source,
            "confidence": round(float(self.confidence), 3),
            "ambiguity": self.ambiguity,
            "clickable": self.clickable,
            "enabled": self.enabled,
            "capture_scope": self.capture_scope,
            "logical_bounds": list(self.logical_bounds) if self.logical_bounds else None,
            "pixel_bounds": list(self.pixel_bounds) if self.pixel_bounds else None,
            "domain": self.domain,
            "path": self.path,
            "alternatives": self.alternatives,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "evidence_digest": self.evidence_digest,
            "identity": self.identity.payload(),
        }


@dataclass(frozen=True)
class EvidenceVerdict:
    """Whether this evidence may be acted on without asking again."""

    usable: bool
    requires_selection: bool
    reason: str
    changed: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {"usable": self.usable, "requires_selection": self.requires_selection, "reason": self.reason, "changed": list(self.changed)}


def evaluate_evidence(
    evidence: TargetEvidence | None,
    current: CaptureIdentity | None,
    *,
    now: float | None = None,
    min_confidence: float = MIN_AUTONOMOUS_CONFIDENCE,
) -> EvidenceVerdict:
    """Decide whether a target may be acted on right now.

    ``requires_selection`` means the user should pick or confirm the target;
    ``usable=False`` without it means observe again first.
    """
    if evidence is None:
        return EvidenceVerdict(False, False, "no_target_evidence")
    if not evidence.identity.geometry_trusted:
        return EvidenceVerdict(False, False, "geometry_untrusted")
    if evidence.expired(now):
        return EvidenceVerdict(False, False, "evidence_expired")
    if current is not None:
        changed = evidence.identity.differences(current)
        if changed:
            # The screen is no longer the screen this target was found on.
            return EvidenceVerdict(False, False, "capture_identity_changed", changed)
        if not current.geometry_trusted:
            return EvidenceVerdict(False, False, "geometry_untrusted")
    if evidence.clickable is False or evidence.enabled is False:
        return EvidenceVerdict(False, True, "target_not_actionable")
    if evidence.ambiguity != "none" or evidence.alternatives > 0:
        return EvidenceVerdict(False, True, "ambiguous_target")
    if evidence.source in VISION_ONLY_SOURCES:
        # Pixels can show a plausible button; only the app or the accessibility
        # tree can say it is the one the user asked for.
        return EvidenceVerdict(False, True, "vision_only_target")
    if float(evidence.confidence) < float(min_confidence):
        return EvidenceVerdict(False, True, "low_confidence")
    if evidence.logical_bounds is None:
        return EvidenceVerdict(False, True, "no_logical_bounds")
    return EvidenceVerdict(True, False, "evidence_current")


def strongest_source(sources: Any) -> str:
    """Pick the highest-priority perception source present."""
    available = {str(source) for source in (sources or ())}
    for source in reversed(PERCEPTION_SOURCES):
        if source in available:
            return source
    return ""


def evidence_from_tool_state(state: dict[str, Any], *, ttl_seconds: float = DEFAULT_EVIDENCE_TTL_SECONDS) -> TargetEvidence | None:
    """Read evidence out of a targeting tool's agent_state.

    The targeting tools already publish the candidate and the observation it
    came from; this reads that rather than making them depend on this module.
    Returns None when the state does not describe a located target, so callers
    fall through to "no evidence" instead of a half-populated one.
    """
    if not isinstance(state, dict):
        return None
    candidate = state.get("target_candidate")
    observation = state.get("observation")
    if not isinstance(candidate, dict) or not isinstance(observation, dict):
        return None
    rect = observation.get("capture_rect") if isinstance(observation.get("capture_rect"), dict) else {}
    screen_bbox = candidate.get("screen_bbox")
    bounds = tuple(int(value) for value in screen_bbox) if isinstance(screen_bbox, (list, tuple)) and len(screen_bbox) == 4 else None
    identity = CaptureIdentity(
        display_layout_digest=str(rect.get("display_layout_digest") or ""),
        display_id=str(rect.get("display_id") or ""),
        window_id=str(observation.get("window_handle") or ""),
        app_id=str(observation.get("title") or ""),
        scale=float(rect.get("capture_scale") or 1.0),
        capture_digest=str(observation.get("screenshot_rel") or ""),
        geometry_trusted=rect.get("geometry_trusted") is not False,
    )
    alternatives = state.get("target_candidates")
    return TargetEvidence(
        target_id=str(candidate.get("target_id") or candidate.get("label") or "target"),
        label=str(candidate.get("label") or ""),
        source=str(candidate.get("source") or "coordinate"),
        confidence=float(candidate.get("confidence") or 0.0),
        identity=identity,
        role=str(candidate.get("role") or ""),
        ambiguity=str(candidate.get("ambiguity") or "none"),
        clickable=candidate.get("clickable"),
        enabled=candidate.get("enabled"),
        logical_bounds=bounds,
        observed_at=float(observation.get("created_at") or time.time()),
        ttl_seconds=ttl_seconds,
        alternatives=max(0, len(alternatives) - 1) if isinstance(alternatives, list) else 0,
    )


def evidence_from_capture(
    target_id: str,
    label: str,
    source: str,
    confidence: float,
    geometry: CaptureGeometry,
    capture_bbox: tuple[int, int, int, int],
    identity: CaptureIdentity,
    **extra: Any,
) -> TargetEvidence:
    """Build evidence, deriving logical bounds through the capture geometry."""
    return TargetEvidence(
        target_id=target_id,
        label=label,
        source=source,
        confidence=float(confidence),
        identity=identity,
        logical_bounds=geometry.capture_bbox_to_logical(capture_bbox),
        pixel_bounds=tuple(int(value) for value in capture_bbox),  # type: ignore[arg-type]
        **extra,
    )
