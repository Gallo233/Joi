from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "visual_detector"
IMAGE_DIFF_DIR = ROOT / "tests" / "fixtures" / "image_verification"
WIDTH = 400
HEIGHT = 225


Shape = tuple[int, int, int, int, tuple[int, int, int]]


CASES: list[dict] = [
    {
        "id": "bottom_hud_primary",
        "image": "canvas_bottom_hud.ppm",
        "query": "点开始任务",
        "shapes": [
            (136, 176, 128, 22, (52, 142, 218)),
            (288, 162, 92, 36, (84, 92, 132)),
        ],
        "expected_regions": ["bottom_controls"],
        "expected_candidate_count": {"min": 1, "max": 3},
        "allowed_bbox_area": [120, 150, 270, 65],
        "approximate_bbox": [288, 162, 92, 36],
        "should_require_selection": True,
    },
    {
        "id": "right_hud_panel",
        "image": "canvas_right_hud.ppm",
        "query": "点右侧操作区",
        "shapes": [(292, 40, 88, 99, (80, 180, 150))],
        "expected_regions": ["sidebar"],
        "expected_candidate_count": {"min": 1, "max": 3},
        "allowed_bbox_area": [245, 20, 145, 140],
        "approximate_bbox": [256, 34, 80, 54],
        "should_require_selection": True,
    },
    {
        "id": "center_canvas_prompt",
        "image": "canvas_center_prompt.ppm",
        "query": "点中间确认",
        "shapes": [(144, 97, 112, 29, (190, 114, 70))],
        "expected_regions": ["main_content"],
        "expected_candidate_count": {"min": 1, "max": 3},
        "allowed_bbox_area": [110, 55, 190, 110],
        "approximate_bbox": [128, 68, 80, 54],
        "should_require_selection": True,
    },
    {
        "id": "game_skill_bar",
        "image": "game_skill_bar.ppm",
        "query": "点底部技能",
        "shapes": [
            (96, 176, 36, 28, (70, 90, 190)),
            (140, 176, 36, 28, (88, 130, 215)),
            (184, 176, 36, 28, (112, 92, 190)),
            (228, 176, 36, 28, (70, 170, 190)),
        ],
        "expected_regions": ["bottom_controls"],
        "expected_candidate_count": {"min": 1, "max": 3},
        "allowed_bbox_area": [80, 150, 210, 65],
        "approximate_bbox": [136, 176, 128, 22],
        "should_require_selection": True,
    },
    {
        "id": "modal_confirm_cancel",
        "image": "modal_confirm_cancel.ppm",
        "query": "点确认按钮",
        "shapes": [
            (92, 62, 216, 104, (45, 52, 74)),
            (112, 134, 76, 28, (120, 124, 136)),
            (212, 134, 76, 28, (220, 126, 74)),
        ],
        "expected_regions": ["main_content"],
        "expected_candidate_count": {"min": 1, "max": 3},
        "allowed_bbox_area": [90, 85, 230, 100],
        "approximate_bbox": [192, 136, 80, 54],
        "should_require_selection": True,
    },
    {
        "id": "radial_skill_menu",
        "image": "radial_skill_menu.ppm",
        "query": "点技能轮盘",
        "shapes": [
            (176, 58, 48, 48, (95, 170, 210)),
            (124, 92, 48, 48, (190, 96, 160)),
            (228, 92, 48, 48, (210, 150, 80)),
            (176, 126, 48, 48, (96, 210, 142)),
        ],
        "expected_regions": ["main_content"],
        "expected_candidate_count": {"min": 1, "max": 3},
        "allowed_bbox_area": [105, 45, 190, 140],
        "approximate_bbox": [128, 68, 80, 54],
        "should_require_selection": True,
    },
    {
        "id": "canvas_button_cluster",
        "image": "canvas_button_cluster.ppm",
        "query": "点右侧按钮",
        "shapes": [
            (262, 92, 92, 30, (70, 160, 215)),
            (262, 130, 92, 30, (86, 124, 210)),
        ],
        "expected_regions": ["sidebar", "bottom_controls"],
        "expected_candidate_count": {"min": 1, "max": 3},
        "allowed_bbox_area": [240, 70, 140, 140],
        "approximate_bbox": [256, 102, 80, 54],
        "should_require_selection": True,
    },
    {
        "id": "video_canvas_controls",
        "image": "video_canvas_controls.ppm",
        "query": "点播放控制",
        "shapes": [
            (0, 182, 400, 28, (38, 42, 56)),
            (136, 176, 128, 22, (60, 120, 220)),
            (24, 184, 28, 22, (230, 232, 236)),
            (330, 184, 44, 22, (92, 98, 118)),
        ],
        "expected_regions": ["bottom_controls"],
        "expected_candidate_count": {"min": 1, "max": 3},
        "allowed_bbox_area": [0, 155, 400, 65],
        "approximate_bbox": [288, 162, 92, 36],
        "should_require_selection": True,
    },
]

IMAGE_DIFF_CASES: list[dict] = [
    {
        "id": "image_diff_large_panel_change",
        "before_image": "large_panel_before.ppm",
        "after_image": "large_panel_after.ppm",
        "before_shapes": [(60, 62, 240, 94, (58, 64, 82))],
        "after_shapes": [(60, 62, 240, 94, (58, 64, 82)), (250, 132, 92, 34, (214, 126, 74))],
        "expected_image_changed": True,
        "expected_verification_status": "changed",
    },
    {
        "id": "image_diff_subtle_visible_change",
        "before_image": "subtle_button_before.ppm",
        "after_image": "subtle_button_after.ppm",
        "before_shapes": [(144, 92, 112, 36, (66, 72, 94))],
        "after_shapes": [(144, 92, 112, 36, (66, 72, 94)), (184, 134, 28, 18, (218, 184, 78))],
        "expected_image_changed": True,
        "expected_verification_status": "changed",
    },
    {
        "id": "image_diff_identical_frame",
        "before_image": "identical_before.ppm",
        "after_image": "identical_after.ppm",
        "before_shapes": [(120, 76, 160, 62, (72, 120, 180))],
        "after_shapes": [(120, 76, 160, 62, (72, 120, 180))],
        "expected_image_changed": False,
        "expected_verification_status": "likely_noop",
    },
    {
        "id": "image_diff_tiny_compression_noise",
        "before_image": "noise_before.ppm",
        "after_image": "noise_after.ppm",
        "base_color": (34, 38, 50),
        "after_base_color": (38, 42, 54),
        "before_shapes": [(120, 76, 160, 62, (72, 120, 180))],
        "after_shapes": [(120, 76, 160, 62, (76, 124, 184))],
        "expected_image_changed": False,
        "expected_verification_status": "likely_noop",
    },
    {
        "id": "image_diff_unreadable_after",
        "before_image": "unreadable_before.ppm",
        "after_image": "unreadable_after.ppm",
        "before_shapes": [(120, 76, 160, 62, (72, 120, 180))],
        "after_unreadable": True,
        "expected_image_changed": None,
        "expected_verification_status": "likely_noop",
    },
]


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIFF_DIR.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for case in CASES:
        image_name = str(case["image"])
        write_ppm(FIXTURE_DIR / image_name, WIDTH, HEIGHT, case["shapes"])
        row = {key: value for key, value in case.items() if key != "shapes"}
        row["image_size"] = [WIDTH, HEIGHT]
        manifest.append(row)
    (FIXTURE_DIR / "visual_cases.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    image_manifest: list[dict] = []
    for case in IMAGE_DIFF_CASES:
        before_name = str(case["before_image"])
        after_name = str(case["after_image"])
        write_ppm(
            IMAGE_DIFF_DIR / before_name,
            WIDTH,
            HEIGHT,
            case.get("before_shapes") or [],
            base_color=tuple(case.get("base_color") or (22, 26, 38)),
        )
        if case.get("after_unreadable"):
            (IMAGE_DIFF_DIR / after_name).write_text("not an image\n", encoding="utf-8")
        else:
            write_ppm(
                IMAGE_DIFF_DIR / after_name,
                WIDTH,
                HEIGHT,
                case.get("after_shapes") or [],
                base_color=tuple(case.get("after_base_color") or case.get("base_color") or (22, 26, 38)),
            )
        row = {
            key: value
            for key, value in case.items()
            if key
            not in {
                "before_shapes",
                "after_shapes",
                "base_color",
                "after_base_color",
                "after_unreadable",
            }
        }
        row["image_size"] = [WIDTH, HEIGHT]
        image_manifest.append(row)
    (IMAGE_DIFF_DIR / "image_diff_cases.json").write_text(json.dumps(image_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {len(manifest)} visual detector fixtures in {FIXTURE_DIR}")
    print(f"generated {len(image_manifest)} image verification fixtures in {IMAGE_DIFF_DIR}")
    return 0


def write_ppm(path: Path, width: int, height: int, shapes: Iterable[Shape], base_color: tuple[int, int, int] | None = None) -> None:
    shape_rows = list(shapes)
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            if base_color is None:
                base = 22 + int(18 * (x / width)) + int(12 * (y / height))
                red, green, blue = base, base + 4, base + 12
            else:
                red, green, blue = base_color
            for sx, sy, sw, sh, color in shape_rows:
                if sx <= x < sx + sw and sy <= y < sy + sh:
                    border = x in (sx, sx + sw - 1) or y in (sy, sy + sh - 1)
                    red, green, blue = (245, 245, 250) if border else color
            pixels.extend((max(0, min(255, red)), max(0, min(255, green)), max(0, min(255, blue))))
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(pixels))


if __name__ == "__main__":
    raise SystemExit(main())
