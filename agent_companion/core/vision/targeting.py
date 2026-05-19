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
    rank: int = 0
    ambiguity: str = "none"
    source: str = "ocr_region"
    reason: str = ""

    def to_agent_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "label": self.label,
            "text": self.text,
            "region_label": self.region_label,
            "confidence": round(float(self.confidence), 3),
            "rank": self.rank,
            "ambiguity": self.ambiguity,
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
    items = list(_iter_region_items(regions))
    candidates: list[TargetCandidate] = []
    for item in items:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        score, reason = _candidate_score(intent, item, text, items)
        if score < 0.35:
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
    return _rank_candidates(candidates)[: max(1, limit)]


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


def _candidate_score(intent: dict[str, Any], item: dict[str, Any], text: str, all_items: list[dict[str, Any]]) -> tuple[float, str]:
    score = 0.0
    reasons: list[str] = []
    text_folded = text.casefold()
    for term in intent["terms"]:
        term_folded = term.casefold()
        match = _text_match_score(term_folded, text_folded)
        if match:
            score += match
            reasons.append("文本匹配" if match >= 0.4 else "文本接近")
            break
    position_score, position_reasons = _position_score(intent, item)
    score += position_score
    reasons.extend(position_reasons)
    duplicate_score, duplicate_reason = _duplicate_position_score(intent, item, text, all_items)
    score += duplicate_score
    if duplicate_reason:
        reasons.append(duplicate_reason)
    confidence = item.get("confidence")
    if isinstance(confidence, (int, float)):
        score += min(1.0, max(0.0, float(confidence))) * 0.18
        reasons.append(f"OCR {round(float(confidence) * 100)}%")
    area_score = _area_score(item)
    if area_score:
        score += area_score
        reasons.append("面积适中")
    region_score = _region_score(str(item.get("region") or "unknown"))
    if region_score:
        score += region_score
        reasons.append("区域相关")
    return min(max(score, 0.0), 1.0), "；".join(dict.fromkeys(reasons))


def _text_match_score(term: str, text: str) -> float:
    if not term:
        return 0.0
    if term == text:
        return 0.45
    if term in text or text in term:
        return 0.42
    if len(term) >= 3 and any(part and part in text for part in _ngrams(term, 2)):
        return 0.24
    return 0.0


def _position_score(intent: dict[str, Any], item: dict[str, Any]) -> tuple[float, list[str]]:
    position = intent["position"]
    if not position:
        return 0.0, []
    score = 0.0
    reasons: list[str] = []
    horizontal = item.get("horizontal")
    vertical = item.get("vertical")
    if position.get("right"):
        if horizontal == "right":
            score += 0.12
            reasons.append("右侧匹配")
        elif horizontal in {"left", "center"}:
            score -= 0.07
    if position.get("left"):
        if horizontal == "left":
            score += 0.12
            reasons.append("左侧匹配")
        elif horizontal in {"right", "center"}:
            score -= 0.07
    if position.get("top"):
        if vertical == "top":
            score += 0.12
            reasons.append("顶部匹配")
        elif vertical in {"middle", "bottom"}:
            score -= 0.07
    if position.get("bottom"):
        if vertical == "bottom":
            score += 0.12
            reasons.append("底部匹配")
        elif vertical in {"top", "middle"}:
            score -= 0.07
    if not intent["terms"] and reasons:
        score += 0.18
        reasons.append("按位置查找")
    return score, reasons


def _duplicate_position_score(intent: dict[str, Any], item: dict[str, Any], text: str, all_items: list[dict[str, Any]]) -> tuple[float, str]:
    if not intent["position"]:
        return 0.0, ""
    normalized = _normalize_text(text)
    duplicates = [row for row in all_items if _normalize_text(str(row.get("text") or "")) == normalized]
    if len(duplicates) <= 1:
        return 0.0, ""
    target = _target_point(intent)
    if target is None:
        return 0.0, ""
    distance = _distance_to_target(item, target)
    if distance is None:
        return 0.0, ""
    return max(0.0, 0.1 * (1.0 - min(distance, 1.0))), "重复文本按位置排序"


def _area_score(item: dict[str, Any]) -> float:
    bbox = _bbox_tuple(item.get("bbox"))
    if bbox is None:
        return 0.0
    _, _, width, height = bbox
    if width <= 0 or height <= 0:
        return 0.0
    image_width = _positive_float(item.get("image_width"))
    image_height = _positive_float(item.get("image_height"))
    if image_width and image_height:
        ratio = (width * height) / (image_width * image_height)
        if 0.00015 <= ratio <= 0.04:
            return 0.08
        if ratio <= 0.08:
            return 0.04
        return 0.0
    area = width * height
    if 200 <= area <= 20000:
        return 0.05
    return 0.0


def _region_score(region: str) -> float:
    return {
        "top_bar": 0.05,
        "bottom_controls": 0.05,
        "main_content": 0.04,
        "sidebar": 0.03,
    }.get(region, 0.0)


def _rank_candidates(candidates: list[TargetCandidate]) -> list[TargetCandidate]:
    if not candidates:
        return []
    top = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    close = bool(second and abs(top.confidence - second.confidence) <= 0.08)
    ranked: list[TargetCandidate] = []
    for index, candidate in enumerate(candidates, start=1):
        ambiguity = "none"
        if index <= 2 and close:
            ambiguity = "close_score"
        elif candidate.confidence < 0.7:
            ambiguity = "low_confidence"
        ranked.append(
            TargetCandidate(
                label=candidate.label,
                text=candidate.text,
                region_label=candidate.region_label,
                bbox=candidate.bbox,
                confidence=candidate.confidence,
                rank=index,
                ambiguity=ambiguity,
                source=candidate.source,
                reason=candidate.reason,
            )
        )
    return ranked


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


def _ngrams(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    return [text[index : index + size] for index in range(0, len(text) - size + 1)]


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _target_point(intent: dict[str, Any]) -> tuple[float, float] | None:
    position = intent["position"]
    if not position:
        return None
    x = 0.5
    y = 0.5
    if position.get("left"):
        x = 0.0
    if position.get("right"):
        x = 1.0
    if position.get("top"):
        y = 0.0
    if position.get("bottom"):
        y = 1.0
    return (x, y)


def _distance_to_target(item: dict[str, Any], target: tuple[float, float]) -> float | None:
    bbox = _bbox_tuple(item.get("bbox"))
    image_width = _positive_float(item.get("image_width"))
    image_height = _positive_float(item.get("image_height"))
    if bbox is None or not image_width or not image_height:
        return None
    center_x = (bbox[0] + bbox[2] / 2) / image_width
    center_y = (bbox[1] + bbox[3] / 2) / image_height
    return ((center_x - target[0]) ** 2 + (center_y - target[1]) ** 2) ** 0.5


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
