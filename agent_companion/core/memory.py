from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def remember(self, kind: str, text: str) -> None:
        cleaned = " ".join((text or "").split()).strip()
        if not cleaned:
            return
        with sqlite3.connect(self.path) as db:
            db.execute(
                "insert into memories(kind, text, created_at) values (?, ?, ?)",
                (kind, cleaned[:1200], time.time()),
            )

    def recent(self, limit: int = 12) -> list[dict[str, str]]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "select kind, text from memories order by id desc limit ?",
                (limit,),
            ).fetchall()
        return [{"kind": kind, "text": text} for kind, text in rows]

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
                    created_at real not null
                )
                """
            )

