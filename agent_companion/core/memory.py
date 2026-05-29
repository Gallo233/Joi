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
        if not self.enabled() or not cleaned or ephemeral or sensitive or _rejection_reason(cleaned):
            return None
        safe_kind = _safe_label(kind, "note")
        safe_source = _safe_label(source, "manual")
        with sqlite3.connect(self.path) as db:
            cursor = db.execute(
                "insert into memories(kind, text, source, created_at, ephemeral, sensitive) values (?, ?, ?, ?, ?, ?)",
                (safe_kind, cleaned[:1200], safe_source, time.time(), int(ephemeral), int(sensitive)),
            )
            memory_id = int(cursor.lastrowid)
            self._index_memory(db, memory_id, safe_kind, cleaned[:1200])
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
        if not self.enabled():
            return {"ok": False, "error": "memory_disabled", "reason": "disabled"}
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
            self._delete_memory_index(db, int(memory_id))
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

    def context(self, limit: int = 8, query: str = "") -> list[dict[str, Any]]:
        if not self.enabled():
            return []
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for memory in self.recall(query, limit) if query else []:
            text = str(memory.get("text") or "")
            cleaned = _clean_memory_text(text)
            if not cleaned or cleaned in seen:
                continue
            rows.append(
                {
                    "kind": str(memory.get("kind") or "note"),
                    "text": cleaned[:400],
                    "source": "semantic_recall",
                    "relevance": memory.get("relevance", 0),
                }
            )
            seen.add(cleaned)
            if len(rows) >= limit:
                return rows[: max(1, int(limit or 8))]
        for note in self._manual_vault_notes(limit=limit):
            cleaned = _clean_memory_text(note)
            if not cleaned or _rejection_reason(cleaned) or cleaned in seen:
                continue
            rows.append({"kind": "vault", "text": cleaned[:400], "source": "vault"})
            seen.add(cleaned)
        for memory in self.recent(limit):
            text = str(memory.get("text") or "")
            cleaned = _clean_memory_text(text)
            if not cleaned or cleaned in seen:
                continue
            rows.append(
                {
                    "kind": str(memory.get("kind") or "note"),
                    "text": cleaned[:400],
                    "source": str(memory.get("source") or "memory"),
                }
            )
            seen.add(cleaned)
            if len(rows) >= limit:
                break
        return rows[: max(1, int(limit or 8))]

    def recall(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not self.enabled():
            return []
        cleaned = _clean_memory_text(query)
        if not cleaned:
            return self.recent(limit)
        rows = self._recall_fts(cleaned, max(1, int(limit or 5)))
        seen_ids = {int(row.get("id") or 0) for row in rows}
        fallback = self._recall_by_score(cleaned, max(1, int(limit or 5)) * 2, seen_ids)
        combined = [*rows, *fallback]
        combined.sort(key=lambda row: (float(row.get("relevance") or 0), float(row.get("created_at") or 0)), reverse=True)
        return combined[: max(1, int(limit or 5))]

    def enabled(self) -> bool:
        with sqlite3.connect(self.path) as db:
            row = db.execute("select value from memory_settings where key = 'enabled'").fetchone()
        return row is None or str(row[0]).strip() != "0"

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "insert or replace into memory_settings(key, value, updated_at) values ('enabled', ?, ?)",
                ("1" if enabled else "0", time.time()),
            )
        return self.status()

    def status(self, *, recent_limit: int = 8, pending_limit: int = 8) -> dict[str, Any]:
        return {
            "enabled": self.enabled(),
            "vault_path": str(self.vault_path),
            "recent": self.recent(recent_limit),
            "pending": self.pending(pending_limit),
        }

    def clear(self) -> dict[str, Any]:
        with sqlite3.connect(self.path) as db:
            db.execute("delete from memories")
            db.execute("delete from memory_candidates")
            self._clear_memory_index(db)
        self._rewrite_vault()
        return {"ok": True}

    def _recall_fts(self, query: str, limit: int) -> list[dict[str, Any]]:
        terms = _fts_query_terms(query)
        if not terms:
            return []
        fts_query = " OR ".join(f'"{term}"' for term in terms[:8])
        try:
            with sqlite3.connect(self.path) as db:
                rows = db.execute(
                    """
                    select m.id, m.kind, m.text, m.source, m.created_at, bm25(memories_fts) as rank
                    from memories_fts
                    join memories m on m.id = memories_fts.rowid
                    where memories_fts match ?
                      and m.ephemeral = 0
                      and m.sensitive = 0
                    order by rank
                    limit ?
                    """,
                    (fts_query, limit),
                ).fetchall()
        except Exception:
            return []
        return [
            {
                "id": int(memory_id),
                "kind": kind,
                "text": text,
                "source": source,
                "created_at": float(created_at),
                "relevance": max(0.0, 10.0 - float(rank or 0)),
            }
            for memory_id, kind, text, source, created_at, rank in rows
        ]

    def _recall_by_score(self, query: str, limit: int, exclude_ids: set[int] | None = None) -> list[dict[str, Any]]:
        exclude_ids = exclude_ids or set()
        scored: list[dict[str, Any]] = []
        for memory in self.recent(200):
            memory_id = int(memory.get("id") or 0)
            if memory_id in exclude_ids:
                continue
            score = _memory_recall_score(query, memory)
            if score <= 0:
                continue
            scored.append({**memory, "relevance": score})
        scored.sort(key=lambda row: (float(row.get("relevance") or 0), float(row.get("created_at") or 0)), reverse=True)
        return scored[:limit]

    @staticmethod
    def _index_memory(db: sqlite3.Connection, memory_id: int, kind: str, text: str) -> None:
        try:
            db.execute("insert or replace into memories_fts(rowid, kind, text) values (?, ?, ?)", (memory_id, kind, text))
        except Exception:
            return

    @staticmethod
    def _delete_memory_index(db: sqlite3.Connection, memory_id: int) -> None:
        try:
            db.execute("delete from memories_fts where rowid = ?", (memory_id,))
        except Exception:
            return

    @staticmethod
    def _clear_memory_index(db: sqlite3.Connection) -> None:
        try:
            db.execute("delete from memories_fts")
        except Exception:
            return

    def _resolve_candidate(self, candidate_id: int, status: str, reason: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "update memory_candidates set status = ?, resolved_at = ?, rejection_reason = ? where id = ?",
                (status, time.time(), reason[:80], int(candidate_id)),
            )

    def _rewrite_vault(self) -> None:
        manual_notes = self._manual_vault_notes(limit=80)
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
        lines.extend(["", "## Manual Notes", ""])
        if manual_notes:
            for note in manual_notes:
                lines.append(f"- {note}")
        else:
            lines.append("_Add user-edited notes here. Unsafe paths, URLs, secrets, screenshots, and logs are ignored at read time._")
        lines.append("")
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self.vault_path.write_text("\n".join(lines), encoding="utf-8")

    def _manual_vault_notes(self, *, limit: int = 40) -> list[str]:
        try:
            text = self.vault_path.read_text(encoding="utf-8")
        except Exception:
            return []
        in_manual = False
        notes: list[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("## "):
                in_manual = line.casefold() == "## manual notes"
                continue
            if not in_manual or not line or line.startswith("_"):
                continue
            if line.startswith("-"):
                line = line[1:].strip()
            cleaned = _clean_memory_text(line)
            if cleaned:
                notes.append(cleaned[:400])
            if len(notes) >= limit:
                break
        return notes

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
            try:
                db.execute(
                    """
                    create virtual table if not exists memories_fts using fts5(
                        kind,
                        text,
                        content='memories',
                        content_rowid='id'
                    )
                    """
                )
                db.execute(
                    """
                    insert into memories_fts(rowid, kind, text)
                    select id, kind, text from memories
                    where id not in (select rowid from memories_fts)
                    """
                )
            except Exception:
                pass
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
            db.execute(
                """
                create table if not exists memory_settings(
                    key text primary key,
                    value text not null,
                    updated_at real not null
                )
                """
            )
            db.execute("insert or ignore into memory_settings(key, value, updated_at) values ('enabled', '1', ?)", (time.time(),))


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


def _fts_query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for term in _recall_terms(query):
        if '"' in term:
            term = term.replace('"', '""')
        if term and term not in terms:
            terms.append(term)
    return terms


def _recall_terms(query: str) -> list[str]:
    value = (query or "").casefold()
    terms: list[str] = []
    for raw in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", value, re.IGNORECASE):
        token = raw.strip()
        if not token:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) <= 3:
                terms.append(token)
            else:
                terms.extend(token[index : index + 2] for index in range(len(token) - 1))
                terms.append(token)
        elif len(token) > 1:
            terms.append(token)
    stopwords = {"知道", "什么", "这个", "那个", "一下", "帮我", "请你"}
    return [term for term in terms if term not in stopwords]


def _memory_recall_score(query: str, memory: dict[str, Any]) -> float:
    terms = _recall_terms(query)
    text = str(memory.get("text") or "").casefold()
    kind = str(memory.get("kind") or "").casefold()
    haystack = f"{kind} {text}"
    score = 0.0
    for term in terms:
        if term in haystack:
            score += max(1.0, min(4.0, len(term) * 0.8))
    if any(token in query for token in ("喜欢", "偏好", "习惯", "更喜欢")):
        if kind.startswith("preference"):
            score += 4.0
        if any(token in text for token in ("喜欢", "偏好", "习惯", "更喜欢")):
            score += 2.0
    if any(token in query for token in ("记得", "知道我", "了解我")) and text:
        score += 0.75
    return score


def _safe_label(value: str, fallback: str) -> str:
    text = (value or "").strip().casefold().replace("-", "_")
    return text[:40] if text.replace("_", "").isalnum() else fallback


def _format_time(timestamp: float) -> str:
    if timestamp <= 0:
        return "unknown-time"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
