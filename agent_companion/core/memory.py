from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def remember(self, kind: str, text: str, *, ephemeral: bool = False, sensitive: bool = False) -> None:
        cleaned = " ".join((text or "").split()).strip()
        if not cleaned or ephemeral or sensitive:
            return
        with sqlite3.connect(self.path) as db:
            db.execute(
                "insert into memories(kind, text, created_at, ephemeral, sensitive) values (?, ?, ?, ?, ?)",
                (kind, cleaned[:1200], time.time(), int(ephemeral), int(sensitive)),
            )

    def recent(self, limit: int = 12, *, include_ephemeral: bool = False, include_sensitive: bool = False) -> list[dict[str, Any]]:
        filters = []
        if not include_ephemeral:
            filters.append("ephemeral = 0")
        if not include_sensitive:
            filters.append("sensitive = 0")
        where = f"where {' and '.join(filters)}" if filters else ""
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                f"select kind, text, ephemeral, sensitive from memories {where} order by id desc limit ?",
                (limit,),
            ).fetchall()
        return [
            {"kind": kind, "text": text, "ephemeral": bool(ephemeral), "sensitive": bool(sensitive)}
            for kind, text, ephemeral, sensitive in rows
        ]

    def clear(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("delete from memories")

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
