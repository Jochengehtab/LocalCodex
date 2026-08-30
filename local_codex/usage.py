from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PRICES_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-sol": (4.00, 20.00),
}
DEFAULT_COMPARISON_MODEL = "gpt-5.6-luna"


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


class UsageStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
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
        self._db.execute(
            """INSERT INTO usage_events
               (created_at, session_id, turn_id, model, input_tokens, output_tokens,
                comparison_model, comparison_cost_usd, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                time.time(), session_id, turn_id, model, usage.input_tokens,
                usage.output_tokens, comparison_model, estimate_cost(usage, comparison_model),
                json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        self._db.commit()

    def summary(self, days: int | None = 30) -> dict[str, Any]:
        cutoff = 0 if days is None else time.time() - max(1, days) * 86400
        rows = self._db.execute(
            """SELECT model, comparison_model, SUM(input_tokens), SUM(output_tokens),
                      SUM(comparison_cost_usd), COUNT(*)
               FROM usage_events WHERE created_at >= ?
               GROUP BY model, comparison_model ORDER BY model""",
            (cutoff,),
        ).fetchall()
        model_rows = []
        total = TokenUsage()
        comparison_cost = 0.0
        calls = 0
        for model, comparison, input_tokens, output_tokens, cost, count in rows:
            usage = TokenUsage(int(input_tokens or 0), int(output_tokens or 0))
            total = total.add(usage)
            comparison_cost += float(cost or 0)
            calls += int(count or 0)
            model_rows.append({
                "model": model,
                "comparison_model": comparison,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "comparison_cost_usd": round(float(cost or 0), 8),
                "calls": int(count or 0),
            })
        return {
            "days": days,
            "calls": calls,
            "input_tokens": total.input_tokens,
            "output_tokens": total.output_tokens,
            "total_tokens": total.total_tokens,
            "comparison_cost_usd": round(comparison_cost, 8),
            "estimated_local_api_cost_usd": 0.0,
            "estimated_saved_usd": round(comparison_cost, 8),
            "models": model_rows,
        }

    def close(self) -> None:
        self._db.close()


def format_summary(summary: dict[str, Any]) -> str:
    period = "gesamt" if summary.get("days") is None else f"letzte {summary['days']} Tage"
    lines = [
        f"Lokale Codex-Nutzung ({period})",
        f"  Aufrufe:       {summary['calls']}",
        f"  Input-Tokens:  {summary['input_tokens']:,}".replace(",", "."),
        f"  Output-Tokens: {summary['output_tokens']:,}".replace(",", "."),
        f"  Gesamt-Tokens: {summary['total_tokens']:,}".replace(",", "."),
        f"  API-Vergleich: ${summary['comparison_cost_usd']:.6f}",
        f"  Gespart:       ${summary['estimated_saved_usd']:.6f}",
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
