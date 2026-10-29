from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

MAX_HISTORY = 100


class QueryHistory:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS query_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL,
                    sql TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    truncated INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 2000")
        return connection

    def add(self, question: str, sql: str, row_count: int, truncated: bool) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO query_history (question, sql, row_count, truncated) VALUES (?, ?, ?, ?)",
                (question[:1000], sql[:8000], row_count, int(truncated)),
            )
            connection.execute(
                "DELETE FROM query_history WHERE id NOT IN "
                "(SELECT id FROM query_history ORDER BY id DESC LIMIT ?)",
                (MAX_HISTORY,),
            )
            return int(cursor.lastrowid)

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, question, sql, row_count, truncated, created_at "
                "FROM query_history ORDER BY id DESC LIMIT ?",
                (MAX_HISTORY,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "question": str(row["question"]),
                "sql": str(row["sql"]),
                "row_count": int(row["row_count"]),
                "truncated": bool(row["truncated"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def clear(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM query_history")