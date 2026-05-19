from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


@dataclass(frozen=True)
class TargetCandidate:
    label: str
    text: str
    region_label: str
    bbox: tuple[int, int, int, int] | None
    confidence: float
    source: str = "ocr_region"
    reason: str = ""

    def to_agent_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "label": self.label,
            "text": self.text,
            "region_label": self.region_label,
            "confidence": round(float(self.confidence), 3),
            "source": self.source,
            "reason": self.reason,
        }
        if self.bbox is not None:
            payload["bbox"] = list(self.bbox)
            payload["center"] = [self.bbox[0] + self.bbox[2] // 2, self.bbox[1] + self.bbox[3] // 2]
        return payload


def resolve_target_candidates(query: str, regions: Iterable[Any], limit: int = 3) -> list[TargetCandidate]:
    intent = _target_intent(query)
    if not intent["terms"] and not intent["position"]:
        return []
    candidates: list[TargetCandidate] = []
    for item in _iter_region_items(regions):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        score, reason = _candidate_score(intent, item, text)
        if score < 0.55:
            continue
        bbox = _bbox_tuple(item.get("bbox"))
        candidates.append(
            TargetCandidate(
                label=_candidate_label(intent, text),
                text=text[:160],
                region_label=str(item.get("region") or "unknown"),
                bbox=bbox,
                confidence=score,
                reason=reason,
            )
        )
    candidates.sort(key=lambda candidate: candidate.confidence, reverse=True)
    return candidates[: max(1, limit)]


def _target_intent(query: str) -> dict[str, Any]:
    text = " ".join((query or "").split()).strip()
    normalized = text.casefold()
    terms = _extract_terms(text)
    position = {
        "right": any(token in text for token in ("右上", "右侧", "右边", "右下", "右上角", "右下角")),
        "left": any(token in text for token in ("左上", "左侧", "左边", "左下", "左上角", "左下角")),
        "top": any(token in text for token in ("右上", "左上", "顶部", "上方", "上面", "上角")),
        "bottom": any(token in text for token in ("右下", "左下", "底部", "下方", "下面", "下角")),
    }
    if "login" in normalized and "登录" not in terms:
        terms.append("login")
    return {"terms": terms, "position": {key: value for key, value in position.items() if value}}


def _extract_terms(query: str) -> list[str]:
    text = query
    for token in (
        "帮我",
        "请",
        "点击",
        "点一下",
        "点",
        "按下",
        "按钮",
        "那个",
        "这个",
        "一下",
        "看看",
        "看下",
        "写了什么",
        "是什么",
        "右上角",
        "左上角",
        "右下角",
        "左下角",
        "右上",
        "左上",
        "右下",
        "左下",
    ):
        text = text.replace(token, " ")
    text = re.sub(r"[，,。.!！？?：:\s]+", " ", text).strip()
    terms = [part.strip() for part in text.split(" ") if len(part.strip()) >= 2]
    return terms[:4]


def _iter_region_items(regions: Iterable[Any]) -> Iterable[dict[str, Any]]:
    for region in regions or []:
        row = region.to_agent_state() if hasattr(region, "to_agent_state") else region
        if not isinstance(row, dict):
            continue
        items = row.get("items")
        if isinstance(items, list) and items:
            for item in items:
                if isinstance(item, dict):
                    merged = dict(item)
                    merged.setdefault("region", row.get("label") or "unknown")
                    yield merged
        else:
            for text in row.get("text_snippets") or []:
                yield {
                    "text": text,
                    "bbox": row.get("bbox"),
                    "confidence": row.get("confidence", 0.6),
                    "region": row.get("label", "unknown"),
                }


def _candidate_score(intent: dict[str, Any], item: dict[str, Any], text: str) -> tuple[float, str]:
    score = 0.0
    reasons: list[str] = []
    text_folded = text.casefold()
    for term in intent["terms"]:
        term_folded = term.casefold()
        if term_folded and (term_folded in text_folded or text_folded in term_folded):
            score += 0.75
            reasons.append("text_match")
            break
    position = intent["position"]
    if position:
        if position.get("right") and item.get("horizontal") == "right":
            score += 0.25
            reasons.append("right_region")
        if position.get("left") and item.get("horizontal") == "left":
            score += 0.25
            reasons.append("left_region")
        if position.get("top") and item.get("vertical") == "top":
            score += 0.25
            reasons.append("top_region")
        if position.get("bottom") and item.get("vertical") == "bottom":
            score += 0.25
            reasons.append("bottom_region")
        if not intent["terms"] and reasons:
            score += 0.25
            reasons.append("position_only")
    confidence = item.get("confidence")
    if isinstance(confidence, (int, float)):
        score *= min(1.0, max(0.35, float(confidence)))
    return min(score, 1.0), "+".join(reasons)


def _candidate_label(intent: dict[str, Any], text: str) -> str:
    for term in intent["terms"]:
        if term.casefold() in text.casefold() or text.casefold() in term.casefold():
            return term[:32]
    return text[:32]


def _bbox_tuple(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return (int(value[0]), int(value[1]), int(value[2]), int(value[3]))
    except (TypeError, ValueError):
        return None
