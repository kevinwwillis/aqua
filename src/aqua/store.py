"""SQLite store for questions and answers."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    project TEXT NOT NULL,
    session_name TEXT NOT NULL,
    session_id TEXT,
    host TEXT,
    title TEXT NOT NULL,
    context TEXT NOT NULL,
    questions TEXT NOT NULL,
    blocking INTEGER NOT NULL,
    status TEXT NOT NULL,
    answer TEXT,
    answered_at REAL,
    set_aside_at REAL
);
CREATE INDEX IF NOT EXISTS questions_status ON questions (status, created_at);
"""

# waiting: the user has not answered. answered: the user sent an answer (or dismissed it: answer.cancelled).
# withdrawn: the agent took the question back before the user answered.
# set_aside_at: the user moved a waiting question to their "Set aside" list (still waiting; the agent sees no change).
STATUSES = ("waiting", "answered", "withdrawn")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(questions)")}
            if "set_aside_at" not in cols:  # databases created before set aside
                self._db.execute("ALTER TABLE questions ADD COLUMN set_aside_at REAL")
                self._db.commit()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        d["questions"] = json.loads(d["questions"])
        d["answer"] = json.loads(d["answer"]) if d["answer"] else None
        d["blocking"] = bool(d["blocking"])
        return d

    def create(self, q: dict) -> dict:
        qid = uuid.uuid4().hex[:12]
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO questions (id, created_at, project, session_name, session_id, host, title, context,"
                " questions, blocking, status) VALUES (?,?,?,?,?,?,?,?,?,?, 'waiting')",
                (qid, time.time(), q["project"], q["session_name"], q.get("session_id"), q.get("host"), q["title"],
                 q["context"], json.dumps(q["questions"]), int(q["blocking"])),
            )
        return self.get(qid)

    def get(self, qid: str) -> dict | None:
        with self._lock:
            return self._row(self._db.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone())

    def list(self, status: str, limit: int | None = 200) -> list[dict]:
        order = "ASC" if status == "waiting" else "DESC"
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM questions WHERE status = ? ORDER BY created_at {order} LIMIT ?",
                (status, -1 if limit is None else limit),
            ).fetchall()
        return [self._row(r) for r in rows]

    def finish(self, qid: str, status: str, answer: dict | None) -> dict | None:
        """Move a waiting question to answered or withdrawn. Returns None if it was not waiting."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE questions SET status = ?, answer = ?, answered_at = ? WHERE id = ? AND status = 'waiting'",
                (status, json.dumps(answer) if answer is not None else None, time.time(), qid),
            )
            if cur.rowcount == 0:
                return None
        return self.get(qid)

    def set_aside(self, qid: str, aside: bool) -> dict | None:
        """Move a waiting question into or out of the Set aside list. Returns None if it is not waiting."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE questions SET set_aside_at = ? WHERE id = ? AND status = 'waiting'",
                (time.time() if aside else None, qid),
            )
            if cur.rowcount == 0:
                return None
        return self.get(qid)
