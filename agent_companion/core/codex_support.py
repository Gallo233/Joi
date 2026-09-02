from __future__ import annotations

import hashlib
import json
from typing import Any

from agent_companion.core.agent_cli import resolve_agent_cli_executable


def codex_executable() -> str:
    """Resolve Codex once through the shared Agent CLI policy."""

    return resolve_agent_cli_executable("codex")


def permission_fingerprint(payload: dict[str, Any]) -> str:
    """Return a deterministic fingerprint for approval binding and resume."""

    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
