from __future__ import annotations

import json
import sqlite3
import threading
import time
import zlib
from pathlib import Path
from typing import Any


def normalize_input(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, str):
        return [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": value}],
            }
        ]
    return []


def _pack(value: Any) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return zlib.compress(raw, level=6)


def _unpack(value: bytes) -> Any:
    return json.loads(zlib.decompress(value).decode("utf-8"))


class ResponseStateStore:
    def __init__(self, path: Path, ttl_seconds: int, max_rows: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.max_rows = max_rows
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS responses (
                response_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                full_input BLOB NOT NULL,
                output BLOB NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        self._db.commit()
        self.prune()

    def expand(self, previous_response_id: str | None, current_input: Any) -> list[dict[str, Any]]:
        current = normalize_input(current_input)
        if not previous_response_id:
            return current
        with self._lock:
            row = self._db.execute(
                "SELECT full_input, output FROM responses WHERE response_id = ?",
                (previous_response_id,),
            ).fetchone()
        if not row:
            return current
        return self._deduplicate([*_unpack(row[0]), *_unpack(row[1]), *current])

    def put(
        self,
        response_id: str,
        session_id: str,
        turn_id: str,
        full_input: list[dict[str, Any]],
        output: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            self._db.execute(
                """
                INSERT OR REPLACE INTO responses
                    (response_id, session_id, turn_id, full_input, output, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    response_id,
                    session_id,
                    turn_id,
                    _pack(full_input),
                    _pack(output),
                    time.time(),
                ),
            )
            self._db.commit()
        self.prune()

    def prune(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        with self._lock:
            self._db.execute("DELETE FROM responses WHERE created_at < ?", (cutoff,))
            self._db.execute(
                """
                DELETE FROM responses WHERE response_id IN (
                    SELECT response_id FROM responses
                    ORDER BY created_at DESC LIMIT -1 OFFSET ?
                )
                """,
                (self.max_rows,),
            )
            self._db.commit()

    @staticmethod
    def _deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for item in items:
            identity = str(item.get("id") or "")
            if not identity:
                identity = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(item)
        return result
