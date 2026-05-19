from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class OcrTextBlock:
    text: str
    bbox: tuple[int, int, int, int] | None = None
    confidence: float | None = None

    def to_agent_state(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": _trim_text(self.text, 160)}
        if self.bbox is not None:
            payload["bbox"] = list(self.bbox)
        if self.confidence is not None:
            payload["confidence"] = round(float(self.confidence), 3)
        return payload


@dataclass(frozen=True)
class OcrResult:
    status: str
    summary: str
    text_blocks: list[OcrTextBlock] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    error: str = ""

    def to_agent_state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "text_blocks": [block.to_agent_state() for block in self.text_blocks[:12]],
            "artifacts": list(self.artifacts),
            "error": self.error,
        }

    def detail_text(self) -> str:
        if self.status == "success" and self.text_blocks:
            snippets = " / ".join(_trim_text(block.text, 48) for block in self.text_blocks[:8])
            return f"OCR：{self.summary}\n可见文字：{snippets}"
        if self.status == "unavailable":
            return f"OCR：{self.summary}"
        return f"OCR：{self.summary or '识别失败。'}"


class OcrExtractor(Protocol):
    def extract(self, image_path: Path) -> OcrResult:
        ...


class UnavailableOcrExtractor:
    def __init__(self, reason: str = "未配置 OCR。") -> None:
        self.reason = reason

    def extract(self, image_path: Path) -> OcrResult:
        return OcrResult("unavailable", self.reason, error="ocr_unavailable")


class PytesseractOcrExtractor:
    def extract(self, image_path: Path) -> OcrResult:
        try:
            from PIL import Image
            import pytesseract
        except Exception:
            return OcrResult("unavailable", "OCR 依赖未安装，暂时只能保存截图。", error="ocr_dependency_missing")
        if not image_path.is_file():
            return OcrResult("failed", "截图文件不存在，OCR 没有执行。", error="screenshot_not_found")
        try:
            image = Image.open(image_path)
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            blocks = _blocks_from_tesseract(data)
            if not blocks:
                return OcrResult("success", "没有识别到清晰文字。", [])
            return OcrResult("success", _ocr_summary(blocks), blocks)
        except Exception:
            return OcrResult("failed", "OCR 没有跑通，截图仍然可查看。", error="ocr_failed")


def _blocks_from_tesseract(data: dict[str, Any]) -> list[OcrTextBlock]:
    count = len(data.get("text") or [])
    blocks: list[OcrTextBlock] = []
    for index in range(count):
        text = str((data.get("text") or [""])[index] or "").strip()
        if not text:
            continue
        confidence = _float_or_none((data.get("conf") or [""])[index])
        if confidence is not None and confidence < 35:
            continue
        left = _int_or_zero((data.get("left") or [0])[index])
        top = _int_or_zero((data.get("top") or [0])[index])
        width = _int_or_zero((data.get("width") or [0])[index])
        height = _int_or_zero((data.get("height") or [0])[index])
        blocks.append(OcrTextBlock(text=_trim_text(text, 160), bbox=(left, top, width, height), confidence=confidence))
        if len(blocks) >= 24:
            break
    return blocks


def _ocr_summary(blocks: list[OcrTextBlock]) -> str:
    snippets = [_trim_text(block.text, 24) for block in blocks[:5] if block.text.strip()]
    if not snippets:
        return "没有识别到清晰文字。"
    return f"识别到 {len(blocks)} 段可见文字，包含：{'、'.join(snippets)}。"


def _trim_text(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split()).strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: max(1, limit - 1)]}…"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
