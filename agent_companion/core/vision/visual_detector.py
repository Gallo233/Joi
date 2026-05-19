from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from agent_companion.core.computer_use.schemas import ComputerObservation


@dataclass(frozen=True)
class VisualCandidate:
    label: str
    bbox: tuple[int, int, int, int]
    confidence: float
    reason: str
    source: str = "visual"
    region: str = "unknown"
    preview: dict[str, Any] = field(default_factory=dict)

    def to_agent_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "label": self.label,
            "bbox": list(self.bbox),
            "confidence": round(float(self.confidence), 3),
            "reason": self.reason,
            "source": self.source,
            "region": self.region,
        }
        if self.preview:
            payload["preview"] = dict(self.preview)
        return payload


@dataclass(frozen=True)
class VisualDetectionResult:
    status: str
    summary: str
    candidates: list[VisualCandidate] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    error: str = ""

    def to_agent_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "summary": self.summary,
            "candidates": [candidate.to_agent_state() for candidate in self.candidates],
            "artifacts": list(self.artifacts),
        }
        if self.error:
            payload["error"] = self.error
        return payload

    def detail_text(self) -> str:
        if self.status == "success":
            if self.candidates:
                return f"视觉候选：找到 {len(self.candidates)} 个可疑交互区域。"
            return "视觉候选：没有发现明显交互区域。"
        if self.status == "unavailable":
            return "视觉候选：当前环境未启用启发式检测。"
        return "视觉候选：检测失败，已继续使用 OCR 和 UI 控件结果。"


class VisualDetector(Protocol):
    def detect(self, observation: ComputerObservation, query: str = "") -> VisualDetectionResult:
        ...


class UnavailableVisualDetector:
    def __init__(self, reason: str = "visual_detector_unavailable") -> None:
        self.reason = reason

    def detect(self, observation: ComputerObservation, query: str = "") -> VisualDetectionResult:
        return VisualDetectionResult("unavailable", "视觉启发式检测不可用。", error=self.reason)


class HeuristicVisualDetector:
    def __init__(self, max_candidates: int = 5) -> None:
        self.max_candidates = max(1, int(max_candidates))

    def detect(self, observation: ComputerObservation, query: str = "") -> VisualDetectionResult:
        if not observation.screenshot_path:
            return VisualDetectionResult("unavailable", "没有可分析的截图。", artifacts=_artifacts(observation), error="missing_screenshot")
        image_path = Path(observation.screenshot_path)
        if not image_path.exists():
            return VisualDetectionResult("unavailable", "截图暂时不可用。", artifacts=_artifacts(observation), error="screenshot_missing")
        try:
            image = _load_grayscale_image(image_path)
            if image is None:
                return VisualDetectionResult("unavailable", "缺少轻量图像处理依赖。", artifacts=_artifacts(observation), error="pillow_missing")
            candidates = self._detect_regions(image, observation, query)
        except Exception:
            return VisualDetectionResult("failed", "视觉启发式检测失败。", artifacts=_artifacts(observation), error="visual_detector_failed")

        if not candidates:
            return VisualDetectionResult("success", "没有发现明显的交互块。", artifacts=_artifacts(observation))
        candidates.sort(key=lambda candidate: candidate.confidence, reverse=True)
        candidates = _dedupe_candidates(candidates)
        return VisualDetectionResult(
            "success",
            f"视觉启发式找到 {len(candidates[: self.max_candidates])} 个可能的交互区域。",
            candidates[: self.max_candidates],
            artifacts=_artifacts(observation),
        )

    def _detect_regions(self, image: "_GrayImage", observation: ComputerObservation, query: str) -> list[VisualCandidate]:
        width, height = image.width, image.height
        label = _label_from_query(query)
        rows: list[VisualCandidate] = []
        seen: set[tuple[int, int, int, int]] = set()
        for bbox, region, reason, prior in _candidate_regions(width, height):
            if bbox in seen:
                continue
            seen.add(bbox)
            score = _region_score(image, bbox, prior)
            if score < 0.46:
                continue
            rows.append(
                VisualCandidate(
                    label=label,
                    bbox=bbox,
                    confidence=score,
                    reason=reason,
                    region=region,
                    preview=_preview(observation, bbox, label, region, score),
                )
            )
        return rows


def _candidate_regions(width: int, height: int) -> list[tuple[tuple[int, int, int, int], str, str, float]]:
    if width <= 0 or height <= 0:
        return []
    regions: list[tuple[tuple[int, int, int, int], str, str, float]] = [
        (_bbox(width * 0.34, height * 0.78, width * 0.32, height * 0.1), "bottom_controls", "底部 HUD 区域", 0.08),
        (_bbox(width * 0.72, height * 0.72, width * 0.23, height * 0.16), "bottom_controls", "右下角操作区", 0.09),
        (_bbox(width * 0.73, height * 0.18, width * 0.22, height * 0.44), "sidebar", "右侧 HUD 面板", 0.06),
        (_bbox(width * 0.36, height * 0.43, width * 0.28, height * 0.13), "main_content", "画面中央交互块", 0.05),
        (_bbox(width * 0.72, height * 0.04, width * 0.22, height * 0.12), "top_bar", "右上角状态区", 0.04),
    ]
    tile_w = max(80, width // 5)
    tile_h = max(54, height // 5)
    for row in range(1, 5):
        for col in range(1, 5):
            left = int((width - tile_w) * col / 5)
            top = int((height - tile_h) * row / 5)
            bbox = (left, top, min(tile_w, width - left), min(tile_h, height - top))
            regions.append((bbox, _region_from_bbox(bbox, width, height), "高对比可疑区域", 0.0))
    return regions


def _region_score(image: "_GrayImage", bbox: tuple[int, int, int, int], prior: float) -> float:
    left, top, width, height = bbox
    if width <= 0 or height <= 0:
        return 0.0
    contrast, edge_mean = image.region_stats(bbox)
    area = width * height
    total = image.width * image.height
    area_ratio = area / total if total else 0.0
    area_bonus = 0.08 if 0.005 <= area_ratio <= 0.08 else 0.03 if area_ratio <= 0.14 else 0.0
    score = 0.18 + min(0.28, contrast / 190.0) + min(0.26, edge_mean / 180.0) + area_bonus + prior
    return min(0.74, max(0.0, score))


@dataclass(frozen=True)
class _GrayImage:
    width: int
    height: int
    pixels: list[int]

    def region_stats(self, bbox: tuple[int, int, int, int]) -> tuple[float, float]:
        left, top, width, height = bbox
        right = min(self.width, left + width)
        bottom = min(self.height, top + height)
        left = max(0, left)
        top = max(0, top)
        values: list[int] = []
        edge_total = 0
        edge_count = 0
        step_x = max(1, (right - left) // 80)
        step_y = max(1, (bottom - top) // 60)
        for y in range(top, bottom, step_y):
            row_offset = y * self.width
            next_row = min(self.height - 1, y + step_y) * self.width
            for x in range(left, right, step_x):
                value = self.pixels[row_offset + x]
                values.append(value)
                nx = min(self.width - 1, x + step_x)
                edge_total += abs(value - self.pixels[row_offset + nx])
                edge_total += abs(value - self.pixels[next_row + x])
                edge_count += 2
        if not values:
            return 0.0, 0.0
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        edge_mean = edge_total / edge_count if edge_count else 0.0
        return math.sqrt(variance), edge_mean


def _load_grayscale_image(path: Path) -> _GrayImage | None:
    if path.suffix.casefold() in {".ppm", ".pnm"}:
        return _load_ppm(path)
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except Exception:
        return None
    with Image.open(path) as raw:
        gray = raw.convert("L")
        width, height = gray.size
        return _GrayImage(width, height, list(gray.getdata()))


def _load_ppm(path: Path) -> _GrayImage | None:
    data = path.read_bytes()
    tokens: list[bytes] = []
    index = 0
    while len(tokens) < 4 and index < len(data):
        while index < len(data) and data[index] in b" \t\r\n":
            index += 1
        if index < len(data) and data[index] == ord("#"):
            while index < len(data) and data[index] not in b"\r\n":
                index += 1
            continue
        start = index
        while index < len(data) and data[index] not in b" \t\r\n":
            index += 1
        if start < index:
            tokens.append(data[start:index])
    if len(tokens) < 4 or tokens[0] not in {b"P6", b"P3"}:
        return None
    width = int(tokens[1])
    height = int(tokens[2])
    max_value = max(1, int(tokens[3]))
    while index < len(data) and data[index] in b" \t\r\n":
        index += 1
    if width <= 0 or height <= 0:
        return None
    if tokens[0] == b"P6":
        raw = data[index : index + width * height * 3]
        if len(raw) < width * height * 3:
            return None
        pixels = [_rgb_to_gray(raw[offset], raw[offset + 1], raw[offset + 2], max_value) for offset in range(0, len(raw), 3)]
        return _GrayImage(width, height, pixels)
    values = [int(part) for part in data[index:].split()]
    if len(values) < width * height * 3:
        return None
    pixels = [_rgb_to_gray(values[offset], values[offset + 1], values[offset + 2], max_value) for offset in range(0, width * height * 3, 3)]
    return _GrayImage(width, height, pixels)


def _rgb_to_gray(red: int, green: int, blue: int, max_value: int) -> int:
    scale = 255 / max_value if max_value != 255 else 1.0
    return int(round((0.299 * red + 0.587 * green + 0.114 * blue) * scale))


def _dedupe_candidates(candidates: list[VisualCandidate]) -> list[VisualCandidate]:
    rows: list[VisualCandidate] = []
    for candidate in candidates:
        if any(_iou(candidate.bbox, existing.bbox) >= 0.22 or _center_distance(candidate.bbox, existing.bbox) < 1.0 for existing in rows):
            continue
        rows.append(candidate)
    return rows


def _iou(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> float:
    lx1, ly1, lw, lh = left
    rx1, ry1, rw, rh = right
    lx2, ly2 = lx1 + lw, ly1 + lh
    rx2, ry2 = rx1 + rw, ry1 + rh
    inter_w = max(0, min(lx2, rx2) - max(lx1, rx1))
    inter_h = max(0, min(ly2, ry2) - max(ly1, ry1))
    inter = inter_w * inter_h
    union = lw * lh + rw * rh - inter
    return inter / union if union else 0.0


def _center_distance(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> float:
    lx, ly = left[0] + left[2] / 2, left[1] + left[3] / 2
    rx, ry = right[0] + right[2] / 2, right[1] + right[3] / 2
    scale = max(left[2], left[3], right[2], right[3], 1)
    return math.sqrt((lx - rx) ** 2 + (ly - ry) ** 2) / scale


def _preview(observation: ComputerObservation, bbox: tuple[int, int, int, int], label: str, region: str, confidence: float) -> dict[str, Any]:
    return {
        "artifact": observation.screenshot_rel,
        "bbox": list(bbox),
        "center": [bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2],
        "image_width": observation.width,
        "image_height": observation.height,
        "label": label,
        "region_label": region,
        "region_name": _friendly_region(region),
        "confidence": round(float(confidence), 3),
        "source": "visual",
    }


def _bbox(left: float, top: float, width: float, height: float) -> tuple[int, int, int, int]:
    return (max(0, int(round(left))), max(0, int(round(top))), max(1, int(round(width))), max(1, int(round(height))))


def _label_from_query(query: str) -> str:
    text = " ".join((query or "").split()).strip()
    for token in ("帮我", "请", "点击", "点一下", "点", "按下", "按钮", "那个", "这个", "一下"):
        text = text.replace(token, " ")
    text = " ".join(text.split()).strip()
    return text[:24] if text else "可交互区域"


def _region_from_bbox(bbox: tuple[int, int, int, int], width: int, height: int) -> str:
    left, top, box_width, box_height = bbox
    center_x = left + box_width / 2
    center_y = top + box_height / 2
    if height and center_y / height < 0.2:
        return "top_bar"
    if height and center_y / height > 0.78:
        return "bottom_controls"
    if width and center_x / width > 0.68:
        return "sidebar"
    return "main_content"


def _friendly_region(region: str) -> str:
    return {
        "top_bar": "顶部栏",
        "sidebar": "侧边区域",
        "main_content": "主内容",
        "bottom_controls": "底部控件",
    }.get(region, "未知区域")


def _artifacts(observation: ComputerObservation) -> list[str]:
    return [observation.screenshot_rel] if observation.screenshot_rel else []
