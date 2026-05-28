from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any


_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\|/(?:Users|home|private|tmp|var|Volumes)/|\\\\|data/agent_companion/|"
    r"\.(?:png|jpg|jpeg|webp|gif|bmp|ppm|json|jsonl|log|txt|ya?ml|sqlite3?|db)\b)",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}\b|\b(?:api[_-]?key|token|secret|password|passwd|bearer)\b)",
    re.IGNORECASE,
)
_ID_RE = re.compile(r"\b(?:task|approval|selection|codex|run|resume)[-_]?[0-9a-f]{6,}\b", re.IGNORECASE)
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_RAW_SCREEN_RE = re.compile(r"\b(?:OCR|screenshot|traceback|stderr|stdout|window_handle|bbox|坐标|截图)\b", re.IGNORECASE)


class MemoryStore:
    def __init__(self, path: Path, vault_path: Path | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.vault_path = vault_path or (self.path.parent / "memory" / "joi_memory_vault.md")
        self._init()
        self._rewrite_vault()

    def remember(
        self,
        kind: str,
        text: str,
        *,
        source: str = "manual",
        ephemeral: bool = False,
        sensitive: bool = False,
    ) -> dict[str, Any] | None:
        cleaned = _clean_memory_text(text)
        if not cleaned or ephemeral or sensitive or _rejection_reason(cleaned):
            return None
        with sqlite3.connect(self.path) as db:
            cursor = db.execute(
                "insert into memories(kind, text, source, created_at, ephemeral, sensitive) values (?, ?, ?, ?, ?, ?)",
                (_safe_label(kind, "note"), cleaned[:1200], _safe_label(source, "manual"), time.time(), int(ephemeral), int(sensitive)),
            )
            memory_id = int(cursor.lastrowid)
        self._rewrite_vault()
        return self.memory(memory_id)

    def propose(
        self,
        kind: str,
        text: str,
        *,
        source: str = "candidate",
        ephemeral: bool = False,
        sensitive: bool = False,
    ) -> dict[str, Any]:
        cleaned = _clean_memory_text(text)
        reason = _rejection_reason(cleaned)
        if not cleaned:
            return {"ok": False, "error": "empty_memory_candidate", "reason": "empty"}
        if ephemeral or sensitive:
            return {"ok": False, "error": "sensitive_memory_candidate", "reason": "ephemeral_or_sensitive"}
        if reason:
            return {"ok": False, "error": "unsafe_memory_candidate", "reason": reason}
        now = time.time()
        with sqlite3.connect(self.path) as db:
            cursor = db.execute(
                """
                insert into memory_candidates(kind, text, source, status, created_at, resolved_at, rejection_reason)
                values (?, ?, ?, 'pending', ?, 0, '')
                """,
                (_safe_label(kind, "note"), cleaned[:1200], _safe_label(source, "candidate"), now),
            )
            candidate_id = int(cursor.lastrowid)
        candidate = self.candidate(candidate_id) or {}
        return {"ok": True, "candidate": candidate}

    def save_candidate(self, candidate_id: int) -> dict[str, Any]:
        candidate = self.candidate(candidate_id)
        if not candidate or candidate.get("status") != "pending":
            return {"ok": False, "error": "memory_candidate_not_pending"}
        memory = self.remember(str(candidate.get("kind") or "note"), str(candidate.get("text") or ""), source=str(candidate.get("source") or "candidate"))
        if not memory:
            self._resolve_candidate(candidate_id, "rejected", "unsafe_on_save")
            return {"ok": False, "error": "unsafe_memory_candidate"}
        self._resolve_candidate(candidate_id, "saved", "")
        return {"ok": True, "memory": memory, "candidate": self.candidate(candidate_id)}

    def reject_candidate(self, candidate_id: int, reason: str = "user_rejected") -> dict[str, Any]:
        candidate = self.candidate(candidate_id)
        if not candidate or candidate.get("status") != "pending":
            return {"ok": False, "error": "memory_candidate_not_pending"}
        self._resolve_candidate(candidate_id, "rejected", _safe_label(reason, "user_rejected"))
        return {"ok": True, "candidate": self.candidate(candidate_id)}

    def delete(self, memory_id: int) -> dict[str, Any]:
        with sqlite3.connect(self.path) as db:
            cursor = db.execute("delete from memories where id = ?", (int(memory_id),))
        self._rewrite_vault()
        return {"ok": bool(cursor.rowcount), "deleted": int(memory_id)}

    def memory(self, memory_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "select id, kind, text, source, created_at, ephemeral, sensitive from memories where id = ?",
                (int(memory_id),),
            ).fetchone()
        return _memory_row(row) if row else None

    def candidate(self, candidate_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                """
                select id, kind, text, source, status, created_at, resolved_at, rejection_reason
                from memory_candidates where id = ?
                """,
                (int(candidate_id),),
            ).fetchone()
        return _candidate_row(row) if row else None

    def pending(self, limit: int = 12) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                """
                select id, kind, text, source, status, created_at, resolved_at, rejection_reason
                from memory_candidates where status = 'pending' order by id desc limit ?
                """,
                (max(1, int(limit or 12)),),
            ).fetchall()
        return [_candidate_row(row) for row in rows]

    def recent(self, limit: int = 12, *, include_ephemeral: bool = False, include_sensitive: bool = False) -> list[dict[str, Any]]:
        filters = []
        if not include_ephemeral:
            filters.append("ephemeral = 0")
        if not include_sensitive:
            filters.append("sensitive = 0")
        where = f"where {' and '.join(filters)}" if filters else ""
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                f"select id, kind, text, source, created_at, ephemeral, sensitive from memories {where} order by id desc limit ?",
                (max(1, int(limit or 12)),),
            ).fetchall()
        return [_memory_row(row) for row in rows]

    def status(self, *, recent_limit: int = 8, pending_limit: int = 8) -> dict[str, Any]:
        return {
            "enabled": True,
            "vault_path": str(self.vault_path),
            "recent": self.recent(recent_limit),
            "pending": self.pending(pending_limit),
        }

    def clear(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("delete from memories")
            db.execute("delete from memory_candidates")
        self._rewrite_vault()

    def _resolve_candidate(self, candidate_id: int, status: str, reason: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "update memory_candidates set status = ?, resolved_at = ?, rejection_reason = ? where id = ?",
                (status, time.time(), reason[:80], int(candidate_id)),
            )

    def _rewrite_vault(self) -> None:
        memories = self.recent(200)
        lines = [
            "# Joi Memory Vault",
            "",
            "This local file is human-readable and can be edited or deleted by the user.",
            "Only approved, privacy-filtered memories are written here.",
            "",
            "## Saved Memories",
            "",
        ]
        if not memories:
            lines.append("_No saved memories yet._")
        for memory in reversed(memories):
            timestamp = _format_time(float(memory.get("created_at") or 0))
            kind = _safe_label(str(memory.get("kind") or "note"), "note")
            source = _safe_label(str(memory.get("source") or "manual"), "manual")
            lines.append(f"- {timestamp} [{kind}/{source}] {memory.get('text')}")
        lines.append("")
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self.vault_path.write_text("\n".join(lines), encoding="utf-8")

    def _init(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                """
                create table if not exists memories(
                    id integer primary key autoincrement,
                    kind text not null,
                    text text not null,
                    created_at real not null,
                    ephemeral integer not null default 0,
                    sensitive integer not null default 0
                )
                """
            )
            columns = {row[1] for row in db.execute("pragma table_info(memories)").fetchall()}
            if "ephemeral" not in columns:
                db.execute("alter table memories add column ephemeral integer not null default 0")
            if "sensitive" not in columns:
                db.execute("alter table memories add column sensitive integer not null default 0")
            if "source" not in columns:
                db.execute("alter table memories add column source text not null default 'legacy'")
            db.execute(
                """
                create table if not exists memory_candidates(
                    id integer primary key autoincrement,
                    kind text not null,
                    text text not null,
                    source text not null,
                    status text not null,
                    created_at real not null,
                    resolved_at real not null default 0,
                    rejection_reason text not null default ''
                )
                """
            )


def _memory_row(row: tuple) -> dict[str, Any]:
    memory_id, kind, text, source, created_at, ephemeral, sensitive = row
    return {
        "id": int(memory_id),
        "kind": kind,
        "text": text,
        "source": source,
        "created_at": float(created_at),
        "ephemeral": bool(ephemeral),
        "sensitive": bool(sensitive),
    }


def _candidate_row(row: tuple) -> dict[str, Any]:
    candidate_id, kind, text, source, status, created_at, resolved_at, rejection_reason = row
    return {
        "id": int(candidate_id),
        "kind": kind,
        "text": text,
        "source": source,
        "status": status,
        "created_at": float(created_at),
        "resolved_at": float(resolved_at or 0),
        "rejection_reason": rejection_reason,
    }


def _clean_memory_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _rejection_reason(text: str) -> str:
    if not text:
        return "empty"
    if _SECRET_RE.search(text):
        return "secret_like"
    if _PATH_RE.search(text):
        return "path_or_file_like"
    if _URL_RE.search(text):
        return "url_like"
    if _ID_RE.search(text):
        return "internal_id_like"
    if _RAW_SCREEN_RE.search(text):
        return "raw_screen_or_log_like"
    return ""


def _safe_label(value: str, fallback: str) -> str:
    text = (value or "").strip().casefold().replace("-", "_")
    return text[:40] if text.replace("_", "").isalnum() else fallback


def _format_time(timestamp: float) -> str:
    if timestamp <= 0:
        return "unknown-time"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
