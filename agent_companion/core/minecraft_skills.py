"""Markdown knowledge notes about how to play, loaded on demand.

Joi's Skill system installs and sandboxes *code*, with digests and provenance
because a script can do anything. What is missing here is different and much
lighter: prose. "The Nether needs obsidian and a flint-and-steel", "our base
stores ore in the barrels on the left" -- knowledge that lives in no data
structure and can only be written down.

Kept separate from the Skill system on purpose. These are never executed, so
they need no sandbox; they are read, bounded, and put in a prompt. The index is
cheap enough to carry in every prompt while the bodies load only when asked for,
which is what keeps the instructions short.
"""

from __future__ import annotations

from pathlib import Path
import re
import threading
from typing import Any


SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MAX_SKILLS = 64
MAX_SKILL_BYTES = 32 * 1024
MAX_SKILL_CHARS = 4_000
MAX_SUMMARY_CHARS = 120


class MinecraftSkillLibrary:
    """Read-only view of ``<workspace>/config/minecraft-skills/*.md``."""

    def __init__(self, workspace: Path) -> None:
        self.root = Path(workspace) / "config" / "minecraft-skills"
        self._lock = threading.RLock()

    def index(self) -> list[dict[str, str]]:
        """Every note's name and one-line summary, for the prompt."""

        rows: list[dict[str, str]] = []
        for path in self._paths():
            summary = _summary_of(_read(path))
            if summary:
                rows.append({"name": path.stem, "summary": summary})
        return rows

    def index_text(self) -> str:
        rows = self.index()
        if not rows:
            return ""
        listed = "；".join(f"{row['name']}（{row['summary']}）" for row in rows[:MAX_SKILLS])
        return f"可加载的玩法笔记：{listed}。需要时调用 minecraft_load_skill 读取，不要凭空猜测这些规矩。\n"

    def load(self, name: str) -> str:
        """One note's body, bounded. An unknown name reads as no note."""

        clean = str(name or "").strip().casefold()
        if not SKILL_NAME.fullmatch(clean):
            return ""
        path = self.root / f"{clean}.md"
        with self._lock:
            if not _within(self.root, path) or not path.is_file():
                return ""
            return _bounded(_read(path), MAX_SKILL_CHARS)

    def _paths(self) -> list[Path]:
        with self._lock:
            try:
                rows = sorted(self.root.glob("*.md"))
            except OSError:
                return []
        return [path for path in rows if SKILL_NAME.fullmatch(path.stem) and _within(self.root, path)][:MAX_SKILLS]


def _within(root: Path, path: Path) -> bool:
    """Refuse anything a symlink or a name could point outside the folder."""

    try:
        return path.resolve().parent == root.resolve()
    except OSError:
        return False


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_SKILL_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _summary_of(text: str) -> str:
    """The first non-heading line, which is how a note says what it is for."""

    for line in str(text or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return _bounded(stripped, MAX_SUMMARY_CHARS)
    return ""


def _bounded(value: Any, limit: int) -> str:
    text = str(value or "")
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    return text.strip()[:limit]
