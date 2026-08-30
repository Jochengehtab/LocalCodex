from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class WebCache:
    """Small, process-safe-enough TTL cache backed by SQLite."""

    def __init__(self, path: Path, max_bytes: int = 256 * 1024 * 1024):
        self.path = path
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.execute(
                """CREATE TABLE IF NOT EXISTS web_cache (
                    key TEXT PRIMARY KEY,
                    value BLOB NOT NULL,
                    expires_at REAL NOT NULL,
                    accessed_at REAL NOT NULL,
                    size INTEGER NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=10)
        database.execute("PRAGMA journal_mode=WAL")
        return database

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.time()
        with self._lock, self._connect() as database:
            row = database.execute(
                "SELECT value, expires_at FROM web_cache WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                return None
            if row[1] <= now:
                database.execute("DELETE FROM web_cache WHERE key = ?", (key,))
                return None
            database.execute(
                "UPDATE web_cache SET accessed_at = ? WHERE key = ?", (now, key)
            )
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return None

    def put(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        now = time.time()
        with self._lock, self._connect() as database:
            database.execute(
                """INSERT OR REPLACE INTO web_cache
                   (key, value, expires_at, accessed_at, size)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, encoded, now + ttl_seconds, now, len(encoded)),
            )
            database.execute("DELETE FROM web_cache WHERE expires_at <= ?", (now,))
            total = database.execute("SELECT COALESCE(SUM(size), 0) FROM web_cache").fetchone()[0]
            while total > self.max_bytes:
                removed = database.execute(
                    "DELETE FROM web_cache WHERE key IN "
                    "(SELECT key FROM web_cache ORDER BY accessed_at LIMIT 32)"
                ).rowcount
                if not removed:
                    break
                total = database.execute(
                    "SELECT COALESCE(SUM(size), 0) FROM web_cache"
                ).fetchone()[0]

