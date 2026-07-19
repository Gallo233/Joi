from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path("/Users/liujialuo/Downloads/已生成图像 4 (1).png")
ASSET_DIR = ROOT / "assets"

CROPS = {
    "joi-front-full": (72, 0, 455, 1024),
    "joi-front-head": (125, 6, 386, 318),
    "joi-face-smile": (1230, 8, 1515, 260),
    "joi-face-wink": (1240, 260, 1515, 510),
    "joi-face-think": (1235, 508, 1518, 728),
}


def connected_white_to_alpha(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    pixels = image.load()
    width, height = image.size
    queue: deque[tuple[int, int]] = deque()
    seen = bytearray(width * height)

    def index(x: int, y: int) -> int:
        return y * width + x

    def is_background(x: int, y: int) -> bool:
        red, green, blue, alpha = pixels[x, y]
        return (
            alpha > 0
            and red >= 244
            and green >= 244
            and blue >= 244
            and max(red, green, blue) - min(red, green, blue) <= 12
        )

    for x in range(width):
        for y in (0, height - 1):
            if is_background(x, y):
                seen[index(x, y)] = 1
                queue.append((x, y))

    for y in range(height):
        for x in (0, width - 1):
            if not seen[index(x, y)] and is_background(x, y):
                seen[index(x, y)] = 1
                queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        red, green, blue, _ = pixels[x, y]
        pixels[x, y] = (red, green, blue, 0)
        for next_x, next_y in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= next_x < width and 0 <= next_y < height and not seen[index(next_x, next_y)] and is_background(next_x, next_y):
                seen[index(next_x, next_y)] = 1
                queue.append((next_x, next_y))

    transparent = {(x, y) for y in range(height) for x in range(width) if pixels[x, y][3] == 0}
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            red, green, blue, alpha = pixels[x, y]
            if alpha == 0:
                continue
            if red >= 238 and green >= 238 and blue >= 238:
                touches_background = any((x + dx, y + dy) in transparent for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
                if touches_background:
                    whiteness = min(red, green, blue)
                    edge_alpha = max(20, min(255, int((255 - whiteness) * 18)))
                    pixels[x, y] = (red, green, blue, min(alpha, edge_alpha))

    return image


def erase_original_head(image: Image.Image) -> Image.Image:
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((45, 0, 305, 328), fill=255)
    draw.rectangle((80, 275, 270, 345), fill=0)
    alpha = image.getchannel("A")
    alpha = Image.composite(Image.new("L", image.size, 0), alpha, mask)
    image.putalpha(alpha)
    return image


def build_assets(source: Path = DEFAULT_SOURCE) -> None:
    if not source.exists():
        raise FileNotFoundError(f"source image not found: {source}")

    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    source_image = Image.open(source).convert("RGBA")

    for name, box in CROPS.items():
        cropped = connected_white_to_alpha(source_image.crop(box))
        output_name = name
        if name == "joi-front-full":
            cropped = erase_original_head(cropped)
            output_name = "joi-body"
        cropped.save(ASSET_DIR / f"{output_name}.png")

    source_image.resize((384, 256), Image.LANCZOS).save(ASSET_DIR / "joi-reference-small.png")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build transparent Joi widget assets from the character sheet.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    build_assets(args.source)
