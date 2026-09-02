from __future__ import annotations

from typing import Any, Callable

from agent_companion.core.background_context import BackgroundContextStore


BackgroundAudit = Callable[[str, dict[str, Any], str], None]


class BackgroundContextService:
    """Commands and audit lifecycle for constrained background summaries."""

    def __init__(self, store: BackgroundContextStore, audit: BackgroundAudit) -> None:
        self.store = store
        self.audit = audit

    def status(self) -> dict[str, Any]:
        return {"ok": True, "background": self.store.status()}

    def configure(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        values = params if isinstance(params, dict) else {}
        enabled = values.get("enabled") if isinstance(values.get("enabled"), bool) else None
        result = self.store.configure(
            enabled=enabled,
            scope_type=str(values.get("scope_type") or ""),
            label=str(values.get("label") or ""),
            active_scope_id=str(values.get("active_scope_id") or ""),
        )
        self.audit(
            "背景上下文设置已更新。" if result.get("ok") else "背景上下文设置没有更新。",
            result.get("background", {}),
            "success" if result.get("ok") else "failed",
        )
        return result

    def clear(self) -> dict[str, Any]:
        result = self.store.clear_context()
        self.audit("背景上下文摘要已清空。", result.get("background", {}), "info")
        return result
