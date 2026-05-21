from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "visual_detector"
IMAGE_DIFF_DIR = ROOT / "tests" / "fixtures" / "image_verification"
SEMANTIC_DIR = ROOT / "tests" / "fixtures" / "semantic_grounding"
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
    {
        "id": "sparse_page_bottom_action_strip",
        "image": "sparse_page_bottom_action_strip.ppm",
        "query": "点底部操作",
        "shapes": [
            (0, 0, 400, 225, (238, 242, 246)),
            (0, 188, 400, 24, (210, 216, 224)),
            (112, 184, 76, 28, (66, 132, 204)),
            (212, 184, 76, 28, (90, 98, 118)),
        ],
        "expected_regions": ["bottom_controls"],
        "expected_candidate_count": {"min": 1, "max": 4},
        "allowed_bbox_area": [80, 155, 240, 65],
        "approximate_bbox": [136, 176, 128, 22],
        "should_require_selection": True,
    },
    {
        "id": "right_bottom_split_controls",
        "image": "right_bottom_split_controls.ppm",
        "query": "点右侧或底部控件",
        "shapes": [
            (300, 46, 64, 28, (62, 160, 180)),
            (300, 88, 64, 28, (72, 118, 205)),
            (260, 178, 98, 24, (208, 142, 68)),
        ],
        "expected_regions": ["sidebar", "bottom_controls"],
        "expected_candidate_count": {"min": 1, "max": 4},
        "allowed_bbox_area": [235, 20, 150, 195],
        "should_require_selection": True,
    },
    {
        "id": "modal_low_contrast_actions",
        "image": "modal_low_contrast_actions.ppm",
        "query": "点确认",
        "shapes": [
            (88, 56, 224, 112, (58, 62, 78)),
            (116, 132, 72, 24, (104, 110, 126)),
            (212, 132, 72, 24, (174, 132, 88)),
        ],
        "expected_regions": ["main_content"],
        "expected_candidate_count": {"min": 1, "max": 4},
        "allowed_bbox_area": [86, 80, 230, 100],
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
    {
        "id": "image_diff_thin_progress_change",
        "before_image": "thin_progress_before.ppm",
        "after_image": "thin_progress_after.ppm",
        "before_shapes": [(82, 82, 236, 72, (60, 66, 84)), (104, 146, 92, 6, (86, 94, 112))],
        "after_shapes": [(82, 82, 236, 72, (60, 66, 84)), (104, 146, 186, 6, (214, 160, 72))],
        "expected_image_changed": True,
        "expected_verification_status": "changed",
    },
    {
        "id": "image_diff_small_badge_change",
        "before_image": "small_badge_before.ppm",
        "after_image": "small_badge_after.ppm",
        "before_shapes": [(88, 70, 224, 96, (62, 68, 86))],
        "after_shapes": [(88, 70, 224, 96, (62, 68, 86)), (286, 76, 18, 18, (220, 92, 80))],
        "expected_image_changed": True,
        "expected_verification_status": "changed",
    },
    {
        "id": "image_diff_cursor_blink_noop",
        "before_image": "cursor_blink_before.ppm",
        "after_image": "cursor_blink_after.ppm",
        "before_shapes": [(92, 76, 216, 82, (68, 76, 96))],
        "after_shapes": [(92, 76, 216, 82, (68, 76, 96)), (202, 98, 2, 26, (236, 238, 242))],
        "expected_image_changed": False,
        "expected_verification_status": "likely_noop",
    },
]

SEMANTIC_CASES: list[dict] = [
    {
        "id": "semantic_uia_ocr_disagreement_selection",
        "image": "semantic_uia_ocr_disagreement.ppm",
        "query": "点目标甲",
        "shapes": [
            (40, 82, 76, 28, (68, 132, 204)),
            (310, 82, 76, 28, (206, 142, 72)),
        ],
        "ocr_blocks": [{"text": "目标甲", "bbox": [40, 82, 76, 28], "confidence": 0.95}],
        "accessibility_elements": [
            {"name": "目标甲", "role": "ButtonControl", "bounds": [310, 82, 76, 28], "enabled": True, "clickable": True, "confidence": 0.92}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_ambiguity": "close_score",
            "min_candidates": 2,
        },
    },
    {
        "id": "semantic_static_uia_text_selection",
        "image": "semantic_static_uia_text.ppm",
        "query": "点目标甲",
        "shapes": [(170, 82, 76, 28, (96, 118, 150))],
        "accessibility_elements": [
            {"name": "目标甲", "role": "TextControl", "bounds": [170, 82, 76, 28], "enabled": True, "clickable": False, "confidence": 0.92}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "accessibility",
            "top_role": "TextControl",
        },
    },
    {
        "id": "semantic_disabled_uia_button_selection",
        "image": "semantic_disabled_uia_button.ppm",
        "query": "点目标甲",
        "shapes": [(170, 82, 76, 28, (116, 122, 136))],
        "accessibility_elements": [
            {"name": "目标甲", "role": "ButtonControl", "bounds": [170, 82, 76, 28], "enabled": False, "clickable": True, "confidence": 0.92}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "accessibility",
            "top_enabled": False,
        },
    },
    {
        "id": "semantic_visual_only_selection",
        "image": "semantic_visual_only.ppm",
        "query": "点开始任务",
        "shapes": [(136, 176, 128, 22, (60, 126, 220))],
        "visual_candidates": [
            {"label": "开始任务", "bbox": [136, 176, 128, 22], "confidence": 0.66, "reason": "底部操作块", "region": "bottom_controls"}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "visual",
        },
    },
    {
        "id": "semantic_close_low_confidence_selection",
        "image": "semantic_close_low_confidence.ppm",
        "query": "点目标乙",
        "shapes": [
            (128, 82, 72, 28, (74, 118, 190)),
            (220, 82, 72, 28, (78, 124, 196)),
        ],
        "ocr_blocks": [
            {"text": "目标乙", "bbox": [128, 82, 72, 28], "confidence": 0.35},
            {"text": "目标乙", "bbox": [220, 82, 72, 28], "confidence": 0.34},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_ambiguity": "close_score",
            "max_top_confidence": 0.72,
            "min_candidates": 2,
        },
    },
    {
        "id": "semantic_no_candidate_clarification",
        "image": "semantic_no_candidate.ppm",
        "query": "点目标甲",
        "shapes": [(140, 78, 120, 48, (74, 88, 112))],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": False,
            "needs_clarification": True,
            "max_candidates": 0,
        },
    },
    {
        "id": "semantic_dense_duplicate_regions_selection",
        "image": "semantic_dense_duplicate_regions.ppm",
        "query": "点目标丙",
        "shapes": [
            (24, 22, 70, 24, (78, 122, 190)),
            (298, 74, 70, 24, (82, 132, 198)),
            (158, 176, 70, 24, (86, 138, 204)),
        ],
        "ocr_blocks": [
            {"text": "目标丙", "bbox": [24, 22, 70, 24], "confidence": 0.88},
            {"text": "目标丙", "bbox": [298, 74, 70, 24], "confidence": 0.87},
            {"text": "目标丙", "bbox": [158, 176, 70, 24], "confidence": 0.86},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_ambiguity": "close_score",
            "min_candidates": 3,
        },
    },
    {
        "id": "semantic_modal_foreground_fused_approval",
        "image": "semantic_modal_foreground_fused.ppm",
        "query": "点目标丁",
        "shapes": [
            (18, 28, 70, 24, (82, 98, 124)),
            (92, 56, 216, 114, (54, 60, 78)),
            (214, 132, 76, 28, (210, 146, 74)),
        ],
        "ocr_blocks": [
            {"text": "目标丁", "bbox": [214, 132, 76, 28], "confidence": 0.96},
            {"text": "目标丁", "bbox": [18, 28, 70, 24], "confidence": 0.42},
        ],
        "accessibility_elements": [
            {"name": "目标丁", "role": "ButtonControl", "bounds": [214, 132, 76, 28], "enabled": True, "clickable": True, "confidence": 0.94}
        ],
        "expected": {
            "requires_approval": True,
            "candidate_selection_required": False,
            "top_source": "fused",
            "top_ambiguity": "none",
            "approval_tool": "computer.click",
        },
    },
    {
        "id": "semantic_modal_foreground_ambiguous_selection",
        "image": "semantic_modal_foreground_ambiguous.ppm",
        "query": "点目标戊",
        "shapes": [
            (90, 54, 220, 112, (56, 62, 78)),
            (114, 132, 74, 28, (104, 116, 136)),
            (214, 132, 74, 28, (208, 144, 78)),
        ],
        "ocr_blocks": [
            {"text": "目标戊", "bbox": [114, 132, 74, 28], "confidence": 0.92},
            {"text": "目标戊", "bbox": [214, 132, 74, 28], "confidence": 0.91},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_ambiguity": "close_score",
            "min_candidates": 2,
        },
    },
    {
        "id": "semantic_dense_table_adjacent_low_confidence_selection",
        "image": "semantic_dense_table_adjacent_low_confidence.ppm",
        "query": "点目标己",
        "shapes": [
            (54, 62, 292, 1, (180, 186, 196)),
            (54, 90, 292, 1, (180, 186, 196)),
            (54, 118, 292, 1, (180, 186, 196)),
            (270, 68, 54, 18, (88, 128, 184)),
            (270, 96, 54, 18, (90, 130, 186)),
        ],
        "ocr_blocks": [
            {"text": "目标己", "bbox": [270, 68, 54, 18], "confidence": 0.35},
            {"text": "目标己", "bbox": [270, 96, 54, 18], "confidence": 0.34},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_ambiguity": "close_score",
            "max_top_confidence": 0.72,
            "min_candidates": 2,
        },
    },
    {
        "id": "semantic_missing_capture_rect_clarification",
        "image": "semantic_missing_capture_rect.ppm",
        "query": "点目标庚",
        "capture_rect": None,
        "shapes": [(166, 88, 76, 28, (68, 132, 204))],
        "ocr_blocks": [{"text": "目标庚", "bbox": [166, 88, 76, 28], "confidence": 0.96}],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": False,
            "needs_clarification": True,
            "no_approval_request": True,
        },
    },
    {
        "id": "semantic_bad_scale_clarification",
        "image": "semantic_bad_scale.ppm",
        "query": "点目标辛",
        "capture_rect": {"screen_x": 0, "screen_y": 0, "width": 400, "height": 225, "scale_x": 6.0, "scale_y": 1.0},
        "shapes": [(166, 88, 76, 28, (68, 132, 204))],
        "ocr_blocks": [{"text": "目标辛", "bbox": [166, 88, 76, 28], "confidence": 0.96}],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": False,
            "needs_clarification": True,
            "no_approval_request": True,
        },
    },
    {
        "id": "semantic_out_of_bounds_uia_clarification",
        "image": "semantic_out_of_bounds_uia.ppm",
        "query": "点目标壬",
        "shapes": [(318, 88, 60, 28, (68, 132, 204))],
        "accessibility_elements": [
            {"name": "目标壬", "role": "ButtonControl", "bounds": [460, 88, 76, 28], "enabled": True, "clickable": True, "confidence": 0.94}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": False,
            "needs_clarification": True,
            "no_approval_request": True,
            "preview_required": False,
        },
    },
    {
        "id": "semantic_dense_visual_only_selection",
        "image": "semantic_dense_visual_only.ppm",
        "query": "点右下操作",
        "shapes": [
            (58, 52, 286, 118, (52, 58, 76)),
            (284, 172, 86, 30, (208, 146, 72)),
            (138, 178, 124, 22, (64, 124, 218)),
        ],
        "visual_candidates": [
            {"label": "右下操作", "bbox": [284, 172, 86, 30], "confidence": 0.68, "reason": "右下高对比操作块", "region": "bottom_controls"}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "visual",
        },
    },
    {
        "id": "semantic_dense_browser_repeated_nav_selection",
        "image": "semantic_dense_browser_repeated_nav.ppm",
        "query": "点目标菜单",
        "shapes": [
            (18, 18, 68, 24, (78, 122, 190)),
            (112, 18, 68, 24, (82, 128, 196)),
            (298, 52, 78, 24, (88, 136, 204)),
            (38, 156, 82, 24, (76, 116, 184)),
        ],
        "ocr_blocks": [
            {"text": "目标菜单", "bbox": [18, 18, 68, 24], "confidence": 0.91},
            {"text": "目标菜单", "bbox": [112, 18, 68, 24], "confidence": 0.9},
            {"text": "目标菜单", "bbox": [298, 52, 78, 24], "confidence": 0.89},
            {"text": "目标菜单", "bbox": [38, 156, 82, 24], "confidence": 0.88},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_ambiguity": "close_score",
            "min_candidates": 3,
        },
    },
    {
        "id": "semantic_settings_static_disabled_neighbor_selection",
        "image": "semantic_settings_static_disabled_neighbor.ppm",
        "query": "点目标开关",
        "shapes": [
            (54, 56, 292, 1, (178, 184, 194)),
            (54, 90, 292, 1, (178, 184, 194)),
            (76, 64, 92, 18, (94, 104, 126)),
            (274, 62, 64, 22, (130, 134, 146)),
            (274, 96, 64, 22, (78, 126, 194)),
        ],
        "ocr_blocks": [
            {"text": "目标开关", "bbox": [274, 62, 64, 22], "confidence": 0.94},
            {"text": "目标开关", "bbox": [274, 96, 64, 22], "confidence": 0.88},
        ],
        "accessibility_elements": [
            {"name": "目标开关", "role": "TextControl", "bounds": [76, 64, 92, 18], "enabled": True, "clickable": False, "confidence": 0.9},
            {"name": "目标开关", "role": "ButtonControl", "bounds": [274, 62, 64, 22], "enabled": False, "clickable": True, "confidence": 0.93},
            {"name": "目标开关", "role": "ButtonControl", "bounds": [274, 96, 64, 22], "enabled": True, "clickable": True, "confidence": 0.87},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "fused",
            "top_ambiguity": "close_score",
            "top_enabled": False,
            "min_candidates": 3,
        },
    },
    {
        "id": "semantic_canvas_hud_visual_ocr_conflict_selection",
        "image": "semantic_canvas_hud_visual_ocr_conflict.ppm",
        "query": "点右下操作",
        "shapes": [
            (56, 50, 288, 120, (50, 58, 78)),
            (136, 176, 128, 22, (66, 124, 216)),
            (284, 172, 86, 30, (208, 146, 72)),
        ],
        "ocr_blocks": [
            {"text": "右下操作", "bbox": [136, 176, 128, 22], "confidence": 0.32}
        ],
        "visual_candidates": [
            {"label": "右下操作", "bbox": [284, 172, 86, 30], "confidence": 0.68, "reason": "右下 HUD 操作块", "region": "bottom_controls"},
            {"label": "右下操作", "bbox": [136, 176, 128, 22], "confidence": 0.67, "reason": "底部 HUD 操作块", "region": "bottom_controls"},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "visual",
            "top_ambiguity": "close_score",
            "min_candidates": 3,
        },
    },
    {
        "id": "semantic_dense_adjacent_actionable_fused_selection",
        "image": "semantic_dense_adjacent_actionable_fused.ppm",
        "query": "点目标行",
        "shapes": [
            (52, 56, 296, 1, (178, 184, 194)),
            (52, 84, 296, 1, (178, 184, 194)),
            (52, 112, 296, 1, (178, 184, 194)),
            (268, 62, 58, 18, (82, 126, 190)),
            (268, 90, 58, 18, (86, 132, 196)),
        ],
        "ocr_blocks": [
            {"text": "目标行", "bbox": [268, 62, 58, 18], "confidence": 0.93},
            {"text": "目标行", "bbox": [268, 90, 58, 18], "confidence": 0.92},
        ],
        "accessibility_elements": [
            {"name": "目标行", "role": "ButtonControl", "bounds": [268, 62, 58, 18], "enabled": True, "clickable": True, "confidence": 0.93},
            {"name": "目标行", "role": "ButtonControl", "bounds": [268, 90, 58, 18], "enabled": True, "clickable": True, "confidence": 0.92},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "fused",
            "top_ambiguity": "close_score",
            "min_candidates": 2,
        },
    },
    {
        "id": "semantic_modal_foreground_background_priority_approval",
        "image": "semantic_modal_foreground_background_priority.ppm",
        "query": "点目标确认",
        "shapes": [
            (36, 34, 82, 26, (88, 104, 132)),
            (68, 54, 264, 132, (52, 58, 78)),
            (220, 142, 78, 28, (210, 146, 72)),
        ],
        "ocr_blocks": [
            {"text": "目标确认", "bbox": [36, 34, 82, 26], "confidence": 0.38},
            {"text": "目标确认", "bbox": [220, 142, 78, 28], "confidence": 0.96},
        ],
        "accessibility_elements": [
            {"name": "目标确认", "role": "ButtonControl", "bounds": [220, 142, 78, 28], "enabled": True, "clickable": True, "confidence": 0.95}
        ],
        "expected": {
            "requires_approval": True,
            "candidate_selection_required": False,
            "top_source": "fused",
            "top_ambiguity": "none",
            "approval_tool": "computer.click",
            "click_x_range": [256, 263],
            "click_y_range": [153, 159],
        },
    },
    {
        "id": "semantic_nested_popover_background_conflict_selection",
        "image": "semantic_nested_popover_background_conflict.ppm",
        "query": "点目标保存",
        "shapes": [
            (44, 48, 90, 26, (86, 128, 190)),
            (170, 42, 190, 138, (48, 56, 76)),
            (236, 128, 90, 26, (208, 146, 72)),
        ],
        "ocr_blocks": [
            {"text": "目标保存", "bbox": [44, 48, 90, 26], "confidence": 0.92},
            {"text": "目标保存", "bbox": [236, 128, 90, 26], "confidence": 0.91},
        ],
        "accessibility_elements": [
            {"name": "目标保存", "role": "ButtonControl", "bounds": [44, 48, 90, 26], "enabled": True, "clickable": True, "confidence": 0.92},
            {"name": "目标保存", "role": "ButtonControl", "bounds": [236, 128, 90, 26], "enabled": True, "clickable": True, "confidence": 0.91},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "fused",
            "top_ambiguity": "close_score",
            "min_candidates": 2,
        },
    },
    {
        "id": "semantic_nonzero_fractional_capture_approval",
        "image": "semantic_nonzero_fractional_capture.ppm",
        "query": "点目标定位",
        "capture_rect": {"screen_x": 120, "screen_y": 80, "width": 320, "height": 180, "scale_x": 1.25, "scale_y": 1.25},
        "shapes": [(200, 96, 80, 32, (70, 132, 204))],
        "ocr_blocks": [{"text": "目标定位", "bbox": [200, 96, 80, 32], "confidence": 0.97}],
        "expected": {
            "requires_approval": True,
            "candidate_selection_required": False,
            "top_source": "ocr",
            "top_ambiguity": "none",
            "approval_tool": "computer.click",
            "click_x_range": [310, 314],
            "click_y_range": [169, 175],
        },
    },
    {
        "id": "semantic_retina_scale_capture_approval",
        "image": "semantic_retina_scale_capture.ppm",
        "query": "点目标视网膜",
        "capture_rect": {"screen_x": 40, "screen_y": 60, "width": 200, "height": 112, "scale_x": 2.0, "scale_y": 2.0},
        "shapes": [(180, 80, 80, 30, (74, 136, 206))],
        "ocr_blocks": [{"text": "目标视网膜", "bbox": [180, 80, 80, 30], "confidence": 0.97}],
        "expected": {
            "requires_approval": True,
            "candidate_selection_required": False,
            "top_source": "ocr",
            "top_ambiguity": "none",
            "approval_tool": "computer.click",
            "click_x_range": [148, 152],
            "click_y_range": [106, 110],
        },
    },
    {
        "id": "semantic_fractional_scale_mismatch_clarification",
        "image": "semantic_fractional_scale_mismatch.ppm",
        "query": "点目标缩放",
        "capture_rect": {"screen_x": 80, "screen_y": 50, "width": 267, "height": 225, "scale_x": 1.5, "scale_y": 1.0},
        "shapes": [(166, 88, 76, 28, (70, 132, 204))],
        "ocr_blocks": [{"text": "目标缩放", "bbox": [166, 88, 76, 28], "confidence": 0.96}],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": False,
            "needs_clarification": True,
            "no_approval_request": True,
        },
    },
    {
        "id": "semantic_edge_offset_low_confidence_selection",
        "image": "semantic_edge_offset_low_confidence.ppm",
        "query": "点目标边缘",
        "shapes": [
            (312, 186, 72, 24, (84, 126, 190)),
            (306, 182, 82, 32, (50, 58, 76)),
        ],
        "visual_candidates": [
            {"label": "目标边缘", "bbox": [312, 186, 72, 24], "confidence": 0.62, "reason": "边缘低置信候选", "region": "bottom_controls"}
        ],
        "accessibility_elements": [
            {"name": "边缘说明", "role": "TextControl", "bounds": [308, 184, 74, 24], "enabled": True, "clickable": False, "confidence": 0.42}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "visual",
            "top_ambiguity": "low_confidence",
            "min_candidates": 1,
        },
    },
    {
        "id": "semantic_stale_uia_snapshot_clarification",
        "image": "semantic_stale_uia_snapshot.ppm",
        "query": "点目标继续",
        "shapes": [
            (42, 38, 260, 124, (54, 62, 82)),
            (124, 126, 92, 28, (74, 136, 206)),
        ],
        "accessibility_elements": [
            {"name": "目标继续", "role": "ButtonControl", "bounds": [680, 420, 82, 28], "enabled": True, "clickable": True, "confidence": 0.96}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": False,
            "needs_clarification": True,
            "no_approval_request": True,
            "top_source": "accessibility",
            "preview_required": False,
        },
    },
    {
        "id": "semantic_clipped_foreground_edge_selection",
        "image": "semantic_clipped_foreground_edge.ppm",
        "query": "点目标边栏",
        "shapes": [
            (0, 0, 400, 225, (34, 40, 58)),
            (324, 42, 76, 134, (52, 62, 84)),
            (358, 88, 42, 30, (86, 148, 208)),
        ],
        "visual_candidates": [
            {"label": "目标边栏", "bbox": [358, 88, 60, 30], "confidence": 0.62, "reason": "裁剪边缘视觉候选", "region": "sidebar"}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "visual",
            "top_ambiguity": "low_confidence",
            "min_candidates": 1,
        },
    },
    {
        "id": "semantic_multi_window_background_conflict_selection",
        "image": "semantic_multi_window_background_conflict.ppm",
        "query": "点目标打开",
        "shapes": [
            (28, 24, 210, 116, (42, 50, 70)),
            (64, 54, 88, 28, (88, 126, 188)),
            (168, 88, 204, 112, (54, 60, 80)),
            (240, 146, 88, 28, (208, 146, 72)),
        ],
        "ocr_blocks": [
            {"text": "目标打开", "bbox": [64, 54, 88, 28], "confidence": 0.93},
            {"text": "目标打开", "bbox": [240, 146, 88, 28], "confidence": 0.92},
        ],
        "accessibility_elements": [
            {"name": "目标打开", "role": "ButtonControl", "bounds": [64, 54, 88, 28], "enabled": True, "clickable": True, "confidence": 0.93},
            {"name": "目标打开", "role": "ButtonControl", "bounds": [240, 146, 88, 28], "enabled": True, "clickable": True, "confidence": 0.92},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "fused",
            "top_ambiguity": "close_score",
            "min_candidates": 2,
        },
    },
    {
        "id": "semantic_truncated_dropdown_oob_selection",
        "image": "semantic_truncated_dropdown_oob.ppm",
        "query": "点目标菜单",
        "shapes": [
            (250, 40, 150, 128, (48, 56, 76)),
            (344, 74, 56, 28, (82, 134, 198)),
            (344, 108, 56, 28, (70, 84, 114)),
        ],
        "visual_candidates": [
            {"label": "目标菜单", "bbox": [344, 74, 72, 28], "confidence": 0.63, "reason": "截断下拉视觉候选", "region": "sidebar"}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "visual",
            "top_ambiguity": "low_confidence",
            "min_candidates": 1,
        },
    },
    {
        "id": "semantic_partial_capture_rect_clarification",
        "image": "semantic_partial_capture_rect.ppm",
        "query": "点目标裁剪",
        "capture_rect": {"screen_x": 80, "screen_y": 40, "width": 260, "height": 150, "scale_x": 1.0, "scale_y": 1.0},
        "shapes": [
            (0, 0, 400, 225, (38, 44, 62)),
            (330, 92, 72, 30, (74, 136, 206)),
        ],
        "ocr_blocks": [{"text": "目标裁剪", "bbox": [330, 92, 72, 30], "confidence": 0.97}],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": False,
            "needs_clarification": True,
            "no_approval_request": True,
            "top_source": "ocr",
        },
    },
    {
        "id": "semantic_partial_capture_rect_trusted_approval",
        "image": "semantic_partial_capture_rect_trusted.ppm",
        "query": "点目标局部",
        "capture_rect": {"screen_x": 80, "screen_y": 40, "width": 260, "height": 150, "scale_x": 1.0, "scale_y": 1.0},
        "shapes": [
            (0, 0, 400, 225, (38, 44, 62)),
            (110, 80, 72, 30, (74, 136, 206)),
        ],
        "ocr_blocks": [{"text": "目标局部", "bbox": [110, 80, 72, 30], "confidence": 0.97}],
        "expected": {
            "requires_approval": True,
            "candidate_selection_required": False,
            "top_source": "ocr",
            "top_ambiguity": "none",
            "approval_tool": "computer.click",
            "click_x_range": [224, 228],
            "click_y_range": [133, 137],
        },
    },
    {
        "id": "semantic_clipped_uia_center_outside_clarification",
        "image": "semantic_clipped_uia_center_outside.ppm",
        "query": "点目标越界",
        "capture_rect": {"screen_x": 100, "screen_y": 80, "width": 200, "height": 150, "scale_x": 1.0, "scale_y": 1.0},
        "shapes": [
            (0, 0, 400, 225, (38, 44, 62)),
            (180, 50, 80, 30, (74, 136, 206)),
        ],
        "accessibility_elements": [
            {"name": "目标越界", "role": "ButtonControl", "bounds": [280, 130, 80, 30], "enabled": True, "clickable": True, "confidence": 0.97}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": False,
            "needs_clarification": True,
            "no_approval_request": True,
            "top_source": "accessibility",
            "top_ambiguity": "none",
        },
    },
    {
        "id": "semantic_clipped_uia_selection_still_untrusted",
        "image": "semantic_clipped_uia_selection_still_untrusted.ppm",
        "query": "点目标选择",
        "capture_rect": {"screen_x": 100, "screen_y": 80, "width": 200, "height": 150, "scale_x": 1.0, "scale_y": 1.0},
        "shapes": [
            (0, 0, 400, 225, (38, 44, 62)),
            (180, 50, 80, 30, (74, 136, 206)),
            (40, 50, 72, 30, (90, 118, 164)),
        ],
        "accessibility_elements": [
            {"name": "目标选择", "role": "ButtonControl", "bounds": [280, 130, 80, 30], "enabled": True, "clickable": True, "confidence": 0.97},
            {"name": "目标选择", "role": "ButtonControl", "bounds": [140, 130, 72, 30], "enabled": True, "clickable": True, "confidence": 0.95},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "accessibility",
            "top_ambiguity": "close_score",
            "min_candidates": 2,
            "selection": {
                "rank": 1,
                "requires_approval": False,
                "coordinate_untrusted": True,
                "no_approval_request": True,
            },
        },
    },
    {
        "id": "semantic_uia_screen_bbox_inside_approval",
        "image": "semantic_uia_screen_bbox_inside.ppm",
        "query": "点目标可信",
        "capture_rect": {"screen_x": 100, "screen_y": 80, "width": 200, "height": 150, "scale_x": 1.0, "scale_y": 1.0},
        "shapes": [
            (0, 0, 400, 225, (38, 44, 62)),
            (50, 40, 80, 30, (74, 136, 206)),
        ],
        "accessibility_elements": [
            {"name": "目标可信", "role": "ButtonControl", "bounds": [150, 120, 80, 30], "enabled": True, "clickable": True, "confidence": 0.97}
        ],
        "expected": {
            "requires_approval": True,
            "candidate_selection_required": False,
            "top_source": "accessibility",
            "top_ambiguity": "none",
            "approval_tool": "computer.click",
            "click_x_range": [188, 192],
            "click_y_range": [133, 137],
        },
    },
    {
        "id": "semantic_multi_monitor_offset_mismatch_clarification",
        "image": "semantic_multi_monitor_offset_mismatch.ppm",
        "query": "点目标偏移",
        "capture_rect": {"screen_x": -1920, "screen_y": 80, "width": 320, "height": 180, "scale_x": 1.0, "scale_y": 1.0},
        "shapes": [
            (0, 0, 400, 225, (36, 42, 60)),
            (290, 50, 80, 30, (74, 136, 206)),
        ],
        "accessibility_elements": [
            {"name": "目标偏移", "role": "ButtonControl", "bounds": [-1630, 130, 80, 30], "enabled": True, "clickable": True, "confidence": 0.97}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": False,
            "needs_clarification": True,
            "no_approval_request": True,
            "top_source": "accessibility",
            "top_ambiguity": "none",
        },
    },
    {
        "id": "semantic_window_moved_between_observations_clarification",
        "image": "semantic_window_moved_between_observations.ppm",
        "query": "点目标移动",
        "capture_rect": {"screen_x": 420, "screen_y": 260, "width": 240, "height": 150, "scale_x": 1.0, "scale_y": 1.0},
        "shapes": [
            (0, 0, 400, 225, (36, 42, 60)),
            (218, 58, 72, 28, (74, 136, 206)),
        ],
        "accessibility_elements": [
            {"name": "目标移动", "role": "ButtonControl", "bounds": [638, 318, 72, 28], "enabled": True, "clickable": True, "confidence": 0.97}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": False,
            "needs_clarification": True,
            "no_approval_request": True,
            "top_source": "accessibility",
            "top_ambiguity": "none",
        },
    },
    {
        "id": "semantic_mixed_monitor_scale_approval",
        "image": "semantic_mixed_monitor_scale.ppm",
        "query": "点目标混合",
        "capture_rect": {"screen_x": -960, "screen_y": 240, "width": 267, "height": 150, "scale_x": 1.5, "scale_y": 1.5},
        "shapes": [
            (0, 0, 400, 225, (38, 44, 62)),
            (210, 90, 90, 30, (74, 136, 206)),
        ],
        "ocr_blocks": [{"text": "目标混合", "bbox": [210, 90, 90, 30], "confidence": 0.97}],
        "expected": {
            "requires_approval": True,
            "candidate_selection_required": False,
            "top_source": "ocr",
            "top_ambiguity": "none",
            "approval_tool": "computer.click",
            "click_x_range": [-792, -788],
            "click_y_range": [308, 312],
        },
    },
    {
        "id": "semantic_mixed_scale_window_overlap_same_label_selection",
        "image": "semantic_mixed_scale_window_overlap_same_label.ppm",
        "query": "点目标同步",
        "capture_rect": {"screen_x": -960, "screen_y": 240, "width": 267, "height": 150, "scale_x": 1.5, "scale_y": 1.5},
        "shapes": [
            (24, 28, 160, 92, (44, 52, 74)),
            (72, 54, 78, 28, (86, 126, 188)),
            (172, 74, 190, 118, (54, 60, 80)),
            (236, 132, 78, 28, (208, 146, 72)),
        ],
        "ocr_blocks": [
            {"text": "目标同步", "bbox": [72, 54, 78, 28], "confidence": 0.93},
            {"text": "目标同步", "bbox": [236, 132, 78, 28], "confidence": 0.92},
        ],
        "accessibility_elements": [
            {"name": "目标同步", "role": "ButtonControl", "bounds": [-912, 276, 52, 19], "enabled": True, "clickable": True, "confidence": 0.93},
            {"name": "目标同步", "role": "ButtonControl", "bounds": [-803, 328, 52, 19], "enabled": True, "clickable": True, "confidence": 0.92},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "fused",
            "top_ambiguity": "close_score",
            "min_candidates": 2,
        },
    },
    {
        "id": "semantic_dense_repeated_actionable_mixed_monitor_selection",
        "image": "semantic_dense_repeated_actionable_mixed_monitor.ppm",
        "query": "点目标执行",
        "capture_rect": {"screen_x": 1440, "screen_y": -180, "width": 320, "height": 180, "scale_x": 1.25, "scale_y": 1.25},
        "shapes": [
            (0, 0, 400, 225, (238, 242, 246)),
            (42, 34, 300, 24, (210, 216, 224)),
            (42, 76, 300, 24, (210, 216, 224)),
            (260, 30, 78, 28, (72, 132, 204)),
            (260, 72, 78, 28, (76, 136, 208)),
            (260, 114, 78, 28, (80, 140, 212)),
        ],
        "ocr_blocks": [
            {"text": "目标执行", "bbox": [260, 30, 78, 28], "confidence": 0.93},
            {"text": "目标执行", "bbox": [260, 72, 78, 28], "confidence": 0.92},
            {"text": "目标执行", "bbox": [260, 114, 78, 28], "confidence": 0.91},
        ],
        "accessibility_elements": [
            {"name": "目标执行", "role": "ButtonControl", "bounds": [1648, -156, 62, 22], "enabled": True, "clickable": True, "confidence": 0.93},
            {"name": "目标执行", "role": "ButtonControl", "bounds": [1648, -122, 62, 22], "enabled": True, "clickable": True, "confidence": 0.92},
            {"name": "目标执行", "role": "ButtonControl", "bounds": [1648, -88, 62, 22], "enabled": True, "clickable": True, "confidence": 0.91},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "fused",
            "top_ambiguity": "close_score",
            "min_candidates": 3,
        },
    },
    {
        "id": "semantic_focus_churn_current_vs_stale_selection",
        "image": "semantic_focus_churn_current_vs_stale.ppm",
        "query": "点目标继续",
        "shapes": [
            (0, 0, 400, 225, (34, 40, 58)),
            (236, 132, 80, 28, (74, 136, 206)),
            (28, 24, 120, 72, (48, 56, 76)),
        ],
        "ocr_blocks": [
            {"text": "目标继续", "bbox": [236, 132, 80, 28], "confidence": 0.92}
        ],
        "accessibility_elements": [
            {"name": "目标继续", "role": "ButtonControl", "bounds": [640, 420, 82, 28], "enabled": True, "clickable": True, "confidence": 0.95}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "accessibility",
            "top_ambiguity": "close_score",
            "min_candidates": 2,
            "preview_required": False,
            "selection": {"rank": 1, "requires_approval": False, "coordinate_untrusted": True, "no_approval_request": True},
        },
    },
    {
        "id": "semantic_focus_churn_old_window_geometry_clarification",
        "image": "semantic_focus_churn_old_window_geometry.ppm",
        "query": "点目标提交",
        "capture_rect": {"screen_x": 860, "screen_y": 180, "width": 320, "height": 180, "scale_x": 1.0, "scale_y": 1.0},
        "shapes": [
            (0, 0, 400, 225, (36, 42, 60)),
            (42, 36, 128, 70, (50, 58, 78)),
            (246, 142, 84, 28, (78, 126, 188)),
        ],
        "accessibility_elements": [
            {"name": "目标提交", "role": "ButtonControl", "bounds": [128, 220, 84, 28], "enabled": True, "clickable": True, "confidence": 0.96}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": False,
            "needs_clarification": True,
            "no_approval_request": True,
            "top_source": "accessibility",
            "top_ambiguity": "none",
            "preview_required": False,
        },
    },
    {
        "id": "semantic_cross_monitor_drag_stale_geometry_clarification",
        "image": "semantic_cross_monitor_drag_stale_geometry.ppm",
        "query": "点目标拖放",
        "capture_rect": {"screen_x": -1280, "screen_y": 80, "width": 400, "height": 225, "scale_x": 1.0, "scale_y": 1.0},
        "shapes": [
            (0, 0, 400, 225, (38, 44, 62)),
            (286, 118, 82, 30, (78, 130, 194)),
        ],
        "accessibility_elements": [
            {"name": "目标拖放", "role": "ButtonControl", "bounds": [2200, 142, 88, 30], "enabled": True, "clickable": True, "confidence": 0.97}
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": False,
            "needs_clarification": True,
            "no_approval_request": True,
            "top_source": "accessibility",
            "top_ambiguity": "none",
            "preview_required": False,
        },
    },
    {
        "id": "semantic_dense_browser_topbar_repeated_actions_selection",
        "image": "semantic_dense_browser_topbar_repeated_actions.ppm",
        "query": "点目标操作",
        "shapes": [
            (0, 0, 400, 225, (238, 242, 246)),
            (18, 16, 74, 24, (74, 128, 204)),
            (112, 16, 74, 24, (76, 130, 206)),
            (206, 16, 74, 24, (78, 132, 208)),
            (300, 16, 74, 24, (80, 134, 210)),
            (42, 78, 316, 108, (224, 228, 236)),
        ],
        "ocr_blocks": [
            {"text": "目标操作", "bbox": [18, 16, 74, 24], "confidence": 0.94},
            {"text": "目标操作", "bbox": [112, 16, 74, 24], "confidence": 0.93},
            {"text": "目标操作", "bbox": [206, 16, 74, 24], "confidence": 0.92},
            {"text": "目标操作", "bbox": [300, 16, 74, 24], "confidence": 0.91},
        ],
        "accessibility_elements": [
            {"name": "目标操作", "role": "ButtonControl", "bounds": [18, 16, 74, 24], "enabled": True, "clickable": True, "confidence": 0.94},
            {"name": "目标操作", "role": "ButtonControl", "bounds": [112, 16, 74, 24], "enabled": True, "clickable": True, "confidence": 0.93},
            {"name": "目标操作", "role": "ButtonControl", "bounds": [206, 16, 74, 24], "enabled": True, "clickable": True, "confidence": 0.92},
            {"name": "目标操作", "role": "ButtonControl", "bounds": [300, 16, 74, 24], "enabled": True, "clickable": True, "confidence": 0.91},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "fused",
            "top_ambiguity": "close_score",
            "min_candidates": 3,
        },
    },
    {
        "id": "semantic_game_canvas_hud_sparse_visual_cluster_selection",
        "image": "semantic_game_canvas_hud_sparse_visual_cluster.ppm",
        "query": "点技能按钮",
        "shapes": [
            (0, 0, 400, 225, (26, 30, 46)),
            (92, 172, 42, 34, (66, 126, 218)),
            (146, 170, 44, 36, (72, 132, 224)),
            (200, 172, 42, 34, (210, 146, 72)),
            (308, 74, 56, 48, (86, 96, 126)),
        ],
        "visual_candidates": [
            {"label": "技能按钮", "bbox": [92, 172, 42, 34], "confidence": 0.69, "reason": "底部 HUD 技能块", "region": "bottom_controls"},
            {"label": "技能按钮", "bbox": [146, 170, 44, 36], "confidence": 0.68, "reason": "底部 HUD 技能块", "region": "bottom_controls"},
            {"label": "技能按钮", "bbox": [200, 172, 42, 34], "confidence": 0.67, "reason": "底部 HUD 技能块", "region": "bottom_controls"},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "visual",
            "top_ambiguity": "close_score",
            "min_candidates": 3,
            "selection": {"rank": 2, "requires_approval": True},
        },
    },
    {
        "id": "semantic_modal_popover_background_competing_selection",
        "image": "semantic_modal_popover_background_competing.ppm",
        "query": "点目标确认",
        "shapes": [
            (24, 30, 90, 28, (76, 126, 190)),
            (168, 46, 188, 134, (50, 58, 78)),
            (236, 132, 90, 28, (210, 146, 72)),
        ],
        "ocr_blocks": [
            {"text": "目标确认", "bbox": [24, 30, 90, 28], "confidence": 0.94},
            {"text": "目标确认", "bbox": [236, 132, 90, 28], "confidence": 0.93},
        ],
        "accessibility_elements": [
            {"name": "目标确认", "role": "ButtonControl", "bounds": [24, 30, 90, 28], "enabled": True, "clickable": True, "confidence": 0.94},
            {"name": "目标确认", "role": "ButtonControl", "bounds": [236, 132, 90, 28], "enabled": True, "clickable": True, "confidence": 0.93},
        ],
        "expected": {
            "requires_approval": False,
            "candidate_selection_required": True,
            "top_source": "fused",
            "top_ambiguity": "close_score",
            "min_candidates": 2,
        },
    },
]


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIFF_DIR.mkdir(parents=True, exist_ok=True)
    SEMANTIC_DIR.mkdir(parents=True, exist_ok=True)
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
            (IMAGE_DIFF_DIR / after_name).write_bytes(b"not an image\n")
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
    semantic_manifest: list[dict] = []
    for case in SEMANTIC_CASES:
        image_name = str(case["image"])
        write_ppm(SEMANTIC_DIR / image_name, WIDTH, HEIGHT, case.get("shapes") or [])
        row = {key: value for key, value in case.items() if key != "shapes"}
        row["image_size"] = [WIDTH, HEIGHT]
        semantic_manifest.append(row)
    (SEMANTIC_DIR / "semantic_cases.json").write_text(json.dumps(semantic_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {len(manifest)} visual detector fixtures in {FIXTURE_DIR}")
    print(f"generated {len(image_manifest)} image verification fixtures in {IMAGE_DIFF_DIR}")
    print(f"generated {len(semantic_manifest)} semantic grounding fixtures in {SEMANTIC_DIR}")
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
