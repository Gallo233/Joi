from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any


class ArtifactService:
    ALLOWED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
    MAX_ARTIFACT_BYTES = 8 * 1024 * 1024

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def read_image(self, artifact: str) -> dict[str, Any]:
        path = self.resolve(artifact)
        if path is None or not path.is_file():
            return {"ok": False, "error": "artifact_not_found"}
        if path.suffix.lower() not in self.ALLOWED_IMAGE_SUFFIXES:
            return {"ok": False, "error": "unsupported_artifact_type"}
        if path.stat().st_size > self.MAX_ARTIFACT_BYTES:
            return {"ok": False, "error": "artifact_too_large"}
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {
            "ok": True,
            "artifact": artifact,
            "mime": mime,
            "data_url": f"data:{mime};base64,{encoded}",
        }

    def resolve(self, artifact: str) -> Path | None:
        value = (artifact or "").strip()
        if not value or "\x00" in value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.workspace / value
        try:
            resolved = candidate.resolve()
            resolved.relative_to(self.workspace)
        except (OSError, ValueError):
            return None
        return resolved
