from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class CharacterHarness:
    id: str
    name: str
    persona: str
    tone: str
    boundaries: list[str] = field(default_factory=list)
    voice: dict[str, str] = field(default_factory=dict)

    def prompt_header(self) -> str:
        rules = "\n".join(f"- {rule}" for rule in self.boundaries)
        return f"角色：{self.name}\n语气：{self.tone}\n人设：{self.persona}\n边界：\n{rules}"


def load_character(path: Path) -> CharacterHarness:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    style: dict[str, Any] = raw.get("style") or {}
    return CharacterHarness(
        id=str(raw.get("id") or "builtin-hikari"),
        name=str(raw.get("name") or "星野澪"),
        persona=str(raw.get("persona") or ""),
        tone=str(style.get("tone") or ""),
        boundaries=[str(item) for item in style.get("boundaries") or []],
        voice={str(k): str(v) for k, v in (raw.get("voice") or {}).items()},
    )

