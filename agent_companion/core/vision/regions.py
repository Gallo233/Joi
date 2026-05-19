from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_companion.core.vision.ocr import OcrResult, OcrTextBlock


REGION_LABELS = ("top_bar", "sidebar", "main_content", "bottom_controls", "unknown")
REGION_LABEL_NAMES = {
    "top_bar": "顶部栏",
    "sidebar": "侧边区域",
    "main_content": "主内容",
    "bottom_controls": "底部控件",
    "unknown": "未知区域",
}


@dataclass(frozen=True)
class OcrRegion:
    label: str
    text_snippets: list[str] = field(default_factory=list)
    bbox: tuple[int, int, int, int] | None = None
    confidence: float | None = None
    source: str = "ocr"
    items: list[dict[str, Any]] = field(default_factory=list)

    def to_agent_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "label": self.label,
            "label_name": REGION_LABEL_NAMES.get(self.label, self.label),
            "text_snippets": list(self.text_snippets[:8]),
            "source": self.source,
        }
        if self.bbox is not None:
            payload["bbox"] = list(self.bbox)
        if self.confidence is not None:
            payload["confidence"] = round(float(self.confidence), 3)
        if self.items:
            payload["items"] = list(self.items[:12])
        return payload


def group_ocr_regions(ocr_result: OcrResult, width: int, height: int) -> list[OcrRegion]:
    if ocr_result.status != "success" or not ocr_result.text_blocks:
        return []
    buckets: dict[str, list[dict[str, Any]]] = {label: [] for label in REGION_LABELS}
    for block in ocr_result.text_blocks:
        label = _classify_block(block, width, height)
        item = _item_from_block(block, width, height, label)
        if item:
            buckets[label].append(item)
    regions: list[OcrRegion] = []
    for label in REGION_LABELS:
        items = buckets[label]
        if not items:
            continue
        regions.append(
            OcrRegion(
                label=label,
                text_snippets=[str(item["text"]) for item in items[:8] if str(item.get("text") or "").strip()],
                bbox=_union_bbox([item.get("bbox") for item in items if item.get("bbox")]),
                confidence=_average_confidence(items),
                items=items,
            )
        )
    return regions


def summarize_ocr_regions(regions: list[OcrRegion]) -> str:
    if not regions:
        return "区域概览：没有足够 OCR 文本可分组。"
    parts: list[str] = []
    for region in regions[:5]:
        snippets = " / ".join(region.text_snippets[:4])
        label = REGION_LABEL_NAMES.get(region.label, region.label)
        if snippets:
            parts.append(f"{label}：{snippets}")
    return "区域概览：" + "；".join(parts) if parts else "区域概览：没有足够 OCR 文本可分组。"


def regions_to_agent_state(regions: list[OcrRegion]) -> list[dict[str, Any]]:
    return [region.to_agent_state() for region in regions]


def _classify_block(block: OcrTextBlock, width: int, height: int) -> str:
    if not block.bbox or width <= 0 or height <= 0:
        return "unknown"
    left, top, box_width, box_height = block.bbox
    center_x = left + box_width / 2
    center_y = top + box_height / 2
    if center_y <= height * 0.18:
        return "top_bar"
    if center_y >= height * 0.82:
        return "bottom_controls"
    if center_x <= width * 0.22 or center_x >= width * 0.78:
        return "sidebar"
    return "main_content"


def _item_from_block(block: OcrTextBlock, width: int, height: int, label: str) -> dict[str, Any]:
    text = " ".join((block.text or "").split()).strip()
    if not text:
        return {}
    item: dict[str, Any] = {
        "text": text[:160],
        "region": label,
        "source": "ocr",
    }
    if block.bbox is not None:
        item["bbox"] = list(block.bbox)
        item["horizontal"] = _horizontal_position(block.bbox, width)
        item["vertical"] = _vertical_position(block.bbox, height)
    if block.confidence is not None:
        item["confidence"] = round(float(block.confidence), 3)
    return item


def _horizontal_position(bbox: tuple[int, int, int, int], width: int) -> str:
    if width <= 0:
        return "unknown"
    center_x = bbox[0] + bbox[2] / 2
    if center_x < width / 3:
        return "left"
    if center_x > width * 2 / 3:
        return "right"
    return "center"


def _vertical_position(bbox: tuple[int, int, int, int], height: int) -> str:
    if height <= 0:
        return "unknown"
    center_y = bbox[1] + bbox[3] / 2
    if center_y < height / 3:
        return "top"
    if center_y > height * 2 / 3:
        return "bottom"
    return "middle"


def _union_bbox(rows: list[Any]) -> tuple[int, int, int, int] | None:
    boxes = [row for row in rows if isinstance(row, list) and len(row) == 4]
    if not boxes:
        return None
    left = min(int(box[0]) for box in boxes)
    top = min(int(box[1]) for box in boxes)
    right = max(int(box[0]) + int(box[2]) for box in boxes)
    bottom = max(int(box[1]) + int(box[3]) for box in boxes)
    return (left, top, max(0, right - left), max(0, bottom - top))


def _average_confidence(items: list[dict[str, Any]]) -> float | None:
    values = [float(item["confidence"]) for item in items if isinstance(item.get("confidence"), (int, float))]
    if not values:
        return None
    return sum(values) / len(values)
