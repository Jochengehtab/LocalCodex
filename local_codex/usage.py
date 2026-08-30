from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .i18n import tr


PRICES_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-sol": (4.00, 20.00),
}
DEFAULT_COMPARISON_MODEL = "gpt-5.6-luna"
PERIOD_SECONDS: dict[str, int | None] = {
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
    "all": None,
}


def comparison_model_name(value: str | None) -> str:
    return value if value in PRICES_USD_PER_MILLION else DEFAULT_COMPARISON_MODEL


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
        )


def usage_from_response(response: dict[str, Any]) -> TokenUsage:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        usage = response.get("usage_metadata")
    if not isinstance(usage, dict):
        return TokenUsage()

    def number(*names: str) -> int:
        for name in names:
            value = usage.get(name)
            if isinstance(value, (int, float)) and value >= 0:
                return int(value)
        return 0

    return TokenUsage(
        input_tokens=number("input_tokens", "prompt_tokens", "prompt_eval_count"),
        output_tokens=number("output_tokens", "completion_tokens", "eval_count"),
    )


def estimate_cost(usage: TokenUsage, comparison_model: str = DEFAULT_COMPARISON_MODEL) -> float:
    input_rate, output_rate = PRICES_USD_PER_MILLION[comparison_model_name(comparison_model)]
    return (usage.input_tokens * input_rate + usage.output_tokens * output_rate) / 1_000_000


def _period_cutoff(period: str) -> float:
    if period not in PERIOD_SECONDS:
        raise ValueError(f"Unsupported period: {period}")
    seconds = PERIOD_SECONDS[period]
    return 0.0 if seconds is None else time.time() - seconds


class UsageStore:
    """Persistent exact Ollama usage grouped by stable Codex session IDs."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                comparison_model TEXT NOT NULL,
                comparison_cost_usd REAL NOT NULL,
                metadata TEXT NOT NULL
            )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT,
                first_seen_at REAL NOT NULL,
                last_seen_at REAL NOT NULL
            )"""
        )
        self._db.execute(
            """INSERT OR IGNORE INTO sessions(session_id, first_seen_at, last_seen_at)
               SELECT session_id, MIN(created_at), MAX(created_at)
               FROM usage_events GROUP BY session_id"""
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_events(created_at)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_session_created ON usage_events(session_id, created_at)"
        )
        self._db.commit()

    def record(
        self,
        *,
        session_id: str,
        turn_id: str,
        model: str,
        usage: TokenUsage,
        comparison_model: str = DEFAULT_COMPARISON_MODEL,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        with self._lock:
            self._db.execute(
                """INSERT INTO usage_events
                   (created_at, session_id, turn_id, model, input_tokens, output_tokens,
                    comparison_model, comparison_cost_usd, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now, session_id, turn_id, model, usage.input_tokens,
                    usage.output_tokens, comparison_model, estimate_cost(usage, comparison_model),
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            self._db.execute(
                """INSERT INTO sessions(session_id, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
                (session_id, now, now),
            )
            self._db.commit()

    def set_session_title(self, session_id: str, title: str) -> None:
        cleaned = " ".join(title.split()).strip()[:120]
        if not cleaned:
            return
        now = time.time()
        with self._lock:
            self._db.execute(
                """INSERT INTO sessions(session_id, title, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     title=excluded.title,
                     last_seen_at=MAX(sessions.last_seen_at, excluded.last_seen_at)""",
                (session_id, cleaned, now, now),
            )
            self._db.commit()

    @staticmethod
    def _where(period: str, session_id: str | None) -> tuple[str, list[Any]]:
        clauses = ["created_at >= ?"]
        values: list[Any] = [_period_cutoff(period)]
        if session_id:
            clauses.append("session_id = ?")
            values.append(session_id)
        return " AND ".join(clauses), values

    def statistics(
        self,
        *,
        period: str = "30d",
        session_id: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = min(200, max(1, int(page_size)))
        where, values = self._where(period, session_id)
        with self._lock:
            aggregate = self._db.execute(
                f"""SELECT COUNT(*), COUNT(DISTINCT turn_id),
                           COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0),
                           COALESCE(SUM(comparison_cost_usd), 0),
                           MIN(created_at), MAX(created_at)
                    FROM usage_events WHERE {where}""",
                values,
            ).fetchone()
            model_rows = self._db.execute(
                f"""SELECT model, comparison_model, COUNT(*), COUNT(DISTINCT turn_id),
                           SUM(input_tokens), SUM(output_tokens), SUM(comparison_cost_usd)
                    FROM usage_events WHERE {where}
                    GROUP BY model, comparison_model ORDER BY SUM(input_tokens + output_tokens) DESC""",
                values,
            ).fetchall()
            session_count = int(self._db.execute(
                f"SELECT COUNT(DISTINCT session_id) FROM usage_events WHERE {where}", values
            ).fetchone()[0])
            session_rows = self._db.execute(
                f"""SELECT e.session_id, COALESCE(s.title, ''), MIN(e.created_at), MAX(e.created_at),
                           COUNT(*), COUNT(DISTINCT e.turn_id), SUM(e.input_tokens),
                           SUM(e.output_tokens), SUM(e.comparison_cost_usd),
                           GROUP_CONCAT(DISTINCT e.model)
                    FROM usage_events e LEFT JOIN sessions s ON s.session_id=e.session_id
                    WHERE {where.replace('created_at', 'e.created_at').replace('session_id', 'e.session_id')}
                    GROUP BY e.session_id ORDER BY MAX(e.created_at) DESC LIMIT ? OFFSET ?""",
                [*values, page_size, (page - 1) * page_size],
            ).fetchall()

        calls, turns, input_tokens, output_tokens, cost, first_at, last_at = aggregate
        models = [
            {
                "model": model,
                "comparison_model": comparison,
                "calls": int(calls),
                "turns": int(model_turns),
                "input_tokens": int(model_input or 0),
                "output_tokens": int(model_output or 0),
                "total_tokens": int(model_input or 0) + int(model_output or 0),
                "comparison_cost_usd": round(float(model_cost or 0), 8),
            }
            for model, comparison, calls, model_turns, model_input, model_output, model_cost
            in model_rows
        ]
        sessions = [
            {
                "session_id": row[0],
                "title": row[1] or tr("session.untitled", id=str(row[0])[:8]),
                "first_seen_at": row[2],
                "last_seen_at": row[3],
                "duration_seconds": max(float(row[3] or 0) - float(row[2] or 0), 0.0),
                "calls": int(row[4] or 0),
                "turns": int(row[5] or 0),
                "input_tokens": int(row[6] or 0),
                "output_tokens": int(row[7] or 0),
                "total_tokens": int(row[6] or 0) + int(row[7] or 0),
                "comparison_cost_usd": round(float(row[8] or 0), 8),
                "models": sorted(filter(None, str(row[9] or "").split(","))),
            }
            for row in session_rows
        ]
        comparison_cost = float(cost or 0)
        return {
            "schema_version": 3,
            "period": period,
            "session_id": session_id,
            "calls": int(calls or 0),
            "turns": int(turns or 0),
            "session_count": session_count,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "total_tokens": int(input_tokens or 0) + int(output_tokens or 0),
            "comparison_cost_usd": round(comparison_cost, 8),
            "estimated_local_api_cost_usd": 0.0,
            "estimated_saved_usd": round(comparison_cost, 8),
            "first_event_at": first_at,
            "last_event_at": last_at,
            "duration_seconds": max(float(last_at or 0) - float(first_at or 0), 0.0),
            "models": models,
            "sessions": sessions,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_items": session_count,
                "total_pages": max(1, (session_count + page_size - 1) // page_size),
            },
        }

    def summary(self, days: int | None = 30) -> dict[str, Any]:
        period = "all" if days is None else ("24h" if days == 1 else "7d" if days == 7 else "30d")
        result = self.statistics(period=period)
        result["days"] = days
        return result

    def export(self, *, period: str = "30d", session_id: str | None = None, format: str = "json") -> str:
        where, values = self._where(period, session_id)
        with self._lock:
            rows = self._db.execute(
                f"""SELECT created_at, session_id, turn_id, model, input_tokens,
                           output_tokens, comparison_model, comparison_cost_usd, metadata
                    FROM usage_events WHERE {where} ORDER BY created_at""",
                values,
            ).fetchall()
        fields = [
            "created_at", "session_id", "turn_id", "model", "input_tokens",
            "output_tokens", "comparison_model", "comparison_cost_usd", "metadata",
        ]
        records = [dict(zip(fields, row)) for row in rows]
        if format == "json":
            for record in records:
                try:
                    record["metadata"] = json.loads(record["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
            return json.dumps(records, ensure_ascii=False, indent=2) + "\n"
        if format != "csv":
            raise ValueError(f"Unsupported export format: {format}")
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
        return output.getvalue()

    def close(self) -> None:
        with self._lock:
            self._db.close()


def format_summary(summary: dict[str, Any]) -> str:
    period = "gesamt" if summary.get("days") is None else f"letzte {summary['days']} Tage"
    lines = [
        f"Lokale Codex-Nutzung ({period})",
        f"  Sitzungen:      {summary.get('session_count', 0)}",
        f"  Turns:          {summary.get('turns', 0)}",
        f"  Aufrufe:        {summary['calls']}",
        f"  Input-Tokens:   {summary['input_tokens']:,}".replace(",", "."),
        f"  Output-Tokens:  {summary['output_tokens']:,}".replace(",", "."),
        f"  Gesamt-Tokens:  {summary['total_tokens']:,}".replace(",", "."),
        f"  API-Vergleich:  ${summary['comparison_cost_usd']:.6f}",
        f"  Gespart:        ${summary['estimated_saved_usd']:.6f}",
        "  Lokale API-Kosten: $0 (Strom, Hardware und Webanbieter-Limits nicht enthalten)",
    ]
    if summary.get("models"):
        lines.append("  Nach Modell:")
        for row in summary["models"]:
            lines.append(
                f"    {row['model']}: {row['input_tokens']:,} in / "
                f"{row['output_tokens']:,} out, Vergleich ${row['comparison_cost_usd']:.6f}"
                .replace(",", ".")
            )
    return "\n".join(lines)
