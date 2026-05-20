from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ScreenshotComparison:
    status: str
    changed: bool | None
    method: str = ""


def compare_screenshots(before: Path | None, after: Path | None, size: int = 64) -> ScreenshotComparison:
    if before is None or after is None:
        return ScreenshotComparison("unavailable", None)
    before_path = Path(before)
    after_path = Path(after)
    if not before_path.is_file() or not after_path.is_file():
        return ScreenshotComparison("unavailable", None)
    if before_path.resolve() == after_path.resolve():
        return ScreenshotComparison("compared", False, "same_file")
    try:
        before_pixels = _load_grayscale_sample(before_path, size)
        after_pixels = _load_grayscale_sample(after_path, size)
    except Exception:
        return ScreenshotComparison("unavailable", None)
    if len(before_pixels) != len(after_pixels) or not before_pixels:
        return ScreenshotComparison("unavailable", None)
    changed = _meaningful_change(before_pixels, after_pixels)
    return ScreenshotComparison("compared", changed, "sampled_pixel_diff")


def _load_grayscale_sample(path: Path, size: int) -> tuple[int, ...]:
    pixels = _load_with_pillow(path, size)
    if pixels is not None:
        return pixels
    return _load_ppm(path, size)


def _load_with_pillow(path: Path, size: int) -> tuple[int, ...] | None:
    try:
        from PIL import Image
    except Exception:
        return None
    with Image.open(path) as image:
        resampling = getattr(getattr(Image, "Resampling", Image), "BILINEAR", 2)
        normalized = image.convert("L").resize((size, size), resampling)
        return tuple(int(value) for value in normalized.getdata())


def _load_ppm(path: Path, size: int) -> tuple[int, ...]:
    data = path.read_bytes()
    tokens, offset = _ppm_header(data)
    if len(tokens) < 4:
        raise ValueError("invalid ppm header")
    magic = tokens[0]
    width = int(tokens[1])
    height = int(tokens[2])
    max_value = int(tokens[3])
    if width <= 0 or height <= 0 or max_value <= 0:
        raise ValueError("invalid ppm dimensions")
    if magic == b"P6":
        raw = data[offset : offset + width * height * 3]
        if len(raw) < width * height * 3:
            raise ValueError("truncated ppm")
        gray = [(raw[index] + raw[index + 1] + raw[index + 2]) // 3 for index in range(0, len(raw), 3)]
    elif magic == b"P3":
        values = [int(token) for token in data[offset:].split()]
        if len(values) < width * height * 3:
            raise ValueError("truncated ppm")
        gray = [(values[index] + values[index + 1] + values[index + 2]) // 3 for index in range(0, width * height * 3, 3)]
    else:
        raise ValueError("unsupported ppm")
    if max_value != 255:
        gray = [max(0, min(255, round(value * 255 / max_value))) for value in gray]
    return _sample_grid(gray, width, height, size)


def _ppm_header(data: bytes) -> tuple[list[bytes], int]:
    tokens: list[bytes] = []
    index = 0
    while index < len(data) and len(tokens) < 4:
        while index < len(data) and data[index] in b" \t\r\n":
            index += 1
        if index < len(data) and data[index] == ord("#"):
            while index < len(data) and data[index] not in b"\r\n":
                index += 1
            continue
        start = index
        while index < len(data) and data[index] not in b" \t\r\n":
            index += 1
        if start != index:
            tokens.append(data[start:index])
    while index < len(data) and data[index] in b" \t\r\n":
        index += 1
    return tokens, index


def _sample_grid(gray: Sequence[int], width: int, height: int, size: int) -> tuple[int, ...]:
    rows: list[int] = []
    for y in range(size):
        source_y = min(height - 1, int((y + 0.5) * height / size))
        for x in range(size):
            source_x = min(width - 1, int((x + 0.5) * width / size))
            rows.append(int(gray[source_y * width + source_x]))
    return tuple(rows)


def _meaningful_change(before: Sequence[int], after: Sequence[int]) -> bool:
    significant = 0
    total_delta = 0
    total = min(len(before), len(after))
    for left, right in zip(before, after):
        delta = abs(int(left) - int(right))
        total_delta += delta
        if delta >= 12:
            significant += 1
    if total <= 0:
        return False
    significant_ratio = significant / total
    return significant_ratio >= 0.003
