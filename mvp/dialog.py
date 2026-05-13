from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


SPECIAL_NAMES = {"NARR", "CHOICE", "STAT", "SCENE", "BGM", "CG"}


@dataclass(frozen=True)
class DialogItem:
    character_name: str
    speech: str
    sprite: str = "1"
    effect: str = ""
    translate: str = ""

    @property
    def is_choice(self) -> bool:
        return self.character_name.upper() == "CHOICE"

    @property
    def is_narration(self) -> bool:
        return self.character_name.upper() == "NARR"

    @property
    def is_scene(self) -> bool:
        return self.character_name.upper() == "SCENE"

    @property
    def options(self) -> list[str]:
        if not self.is_choice:
            return []
        return [part.strip() for part in self.speech.split("/") if part.strip()]


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _json_substring(text: str) -> str:
    cleaned = _strip_code_fence(text)
    if cleaned.startswith("{") or cleaned.startswith("["):
        return cleaned

    candidates = [
        (cleaned.find("{"), cleaned.rfind("}")),
        (cleaned.find("["), cleaned.rfind("]")),
    ]
    valid = [(start, end) for start, end in candidates if start >= 0 and end > start]
    if not valid:
        raise ValueError("LLM response did not contain JSON")
    start, end = min(valid, key=lambda pair: pair[0])
    return cleaned[start : end + 1]


def _coerce_dialog_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        dialog = payload.get("dialog")
        if isinstance(dialog, list):
            return [row for row in dialog if isinstance(row, dict)]
        return [payload]
    return []


def parse_dialog_response(text: str) -> list[DialogItem]:
    payload = json.loads(_json_substring(text))
    items: list[DialogItem] = []

    for row in _coerce_dialog_rows(payload):
        name = str(row.get("character_name") or row.get("name") or "NARR").strip()
        speech = str(row.get("speech") or row.get("text") or "").strip()
        sprite = str(row.get("sprite") or row.get("asset_id") or "1").strip()
        effect = str(row.get("effect") or "").strip()
        translate = str(row.get("translate") or row.get("voice_text") or "").strip()
        if not speech:
            continue
        items.append(
            DialogItem(
                character_name=name,
                speech=speech,
                sprite=sprite or "1",
                effect=effect,
                translate=translate,
            )
        )

    if not items:
        raise ValueError("LLM response JSON did not contain any dialog items")
    return items
