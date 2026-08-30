"""Small in-memory telemetry surface for the optional Windows monitor.

The monitor only consumes short reasoning *summaries*.  It deliberately does not
retain or expose raw reasoning text.  This module has no disk writes and is kept
independent from inference so a closed monitor cannot slow down a turn.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from .usage import TokenUsage


MAX_SUMMARY_CHARS = 700


def _summary_text(value: Any) -> str:
    """Flatten the OpenAI/Ollama reasoning-summary shapes into a safe preview."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for part in value:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            text = part.get("text") or part.get("summary_text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


class TelemetryHub:
    """Thread-safe, bounded status for exactly one serialized local inference."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = self._idle_state()

    @staticmethod
    def _idle_state() -> dict[str, Any]:
        return {
            "active": False,
            "phase": "idle",
            "model": None,
            "turn_id": None,
            "session_id": None,
            "started_at": None,
            "generation_started_at": None,
            "updated_at": time.time(),
            "estimated_output_tokens": 0,
            "estimated_visible_tokens": 0,
            "estimated_reasoning_tokens": 0,
            "input_tokens": None,
            "output_tokens": None,
            "tokens_per_second": None,
            "tokens_per_second_estimated": False,
            "thinking_summary": "",
            "last_tool": None,
            "comparison_cost_usd": None,
            "comparison_model": None,
            "error": None,
            "metric_source": "ollama.responses.stream",
            "ollama_runtime": {},
            "ollama_version": None,
        }

    def start(self, *, model: str, session_id: str, turn_id: str) -> None:
        now = time.time()
        with self._lock:
            self._state = self._idle_state()
            self._state.update(
                active=True,
                phase="loading",
                model=model,
                session_id=session_id,
                turn_id=turn_id,
                started_at=now,
                updated_at=now,
            )

    def phase(self, value: str, *, tool: str | None = None) -> None:
        with self._lock:
            self._state["phase"] = value
            if tool:
                self._state["last_tool"] = tool
            self._state["updated_at"] = time.time()

    def output_delta(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            now = time.time()
            self._state["phase"] = "generating"
            # A dependency-free, intentionally labelled estimate for the live UI.
            estimate = max(1, len(text) // 4)
            self._state["estimated_output_tokens"] += estimate
            self._state["estimated_visible_tokens"] += estimate
            self._state["generation_started_at"] = self._state["generation_started_at"] or now
            self._state["updated_at"] = now

    def reasoning_summary_delta(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            now = time.time()
            self._state["phase"] = "thinking"
            combined = self._state["thinking_summary"] + text
            self._state["thinking_summary"] = combined[-MAX_SUMMARY_CHARS:]
            # Reasoning summaries are a separate stream from the assistant's
            # visible answer.  Do not add their characters to output_tokens:
            # doing so made the live estimate disagree with Ollama's final
            # usage counter.
            self._state["estimated_reasoning_tokens"] += max(1, len(text) // 4)
            self._state["generation_started_at"] = self._state["generation_started_at"] or now
            self._state["updated_at"] = now

    def reasoning_item(self, item: dict[str, Any]) -> None:
        summary = _summary_text(item.get("summary"))
        if not summary:
            return
        with self._lock:
            existing = self._state["thinking_summary"]
            # Servers often send the same summary first as deltas and once as
            # the completed reasoning item.  Prefer the completed spelling
            # instead of showing it twice in the compact tray preview.
            if summary.endswith(existing) or existing.endswith(summary):
                combined = summary if len(summary) >= len(existing) else existing
            else:
                combined = (existing + "\n" + summary).strip()
            self._state["phase"] = "thinking"
            self._state["thinking_summary"] = combined[-MAX_SUMMARY_CHARS:]
            self._state["updated_at"] = time.time()

    def update_ollama_runtime(self, payload: dict[str, Any], version: str | None = None) -> None:
        models = payload.get("models") if isinstance(payload, dict) else None
        runtime: dict[str, Any] = {}
        with self._lock:
            active = self._state.get("model")
        if isinstance(models, list) and models:
            candidate = next(
                (item for item in models if isinstance(item, dict) and item.get("name") == active),
                models[0],
            )
            if isinstance(candidate, dict):
                details = candidate.get("details") if isinstance(candidate.get("details"), dict) else {}
                runtime = {
                    "name": candidate.get("name") or candidate.get("model"),
                    "parameter_size": details.get("parameter_size"),
                    "quantization_level": details.get("quantization_level"),
                    "size_bytes": candidate.get("size"),
                    "size_vram_bytes": candidate.get("size_vram"),
                    "context_length": candidate.get("context_length"),
                    "expires_at": candidate.get("expires_at"),
                    "source": "ollama.api.ps",
                }
        with self._lock:
            self._state["ollama_runtime"] = runtime
            if version:
                self._state["ollama_version"] = version

    def complete(
        self,
        usage: TokenUsage,
        *,
        comparison_cost_usd: float,
        comparison_model: str,
        native_timing: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        with self._lock:
            started = self._state.get("started_at") or now
            generation_started = self._state.get("generation_started_at") or started
            generation_elapsed = max(now - float(generation_started), 0.001)
            timing = native_timing or {}
            nested_usage = timing.get("usage")
            if isinstance(nested_usage, dict):
                timing = {**nested_usage, **timing}
            native_eval_count = timing.get("eval_count")
            native_eval_duration = timing.get("eval_duration")
            if isinstance(native_eval_count, (int, float)) and isinstance(native_eval_duration, (int, float)) and native_eval_duration > 0:
                tokens_per_second = float(native_eval_count) / (float(native_eval_duration) / 1_000_000_000)
                source = "ollama.eval_duration"
            else:
                tokens_per_second = usage.output_tokens / generation_elapsed
                source = "ollama.responses.usage+stream_clock"
            self._state.update(
                active=False,
                phase="completed",
                updated_at=now,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                estimated_output_tokens=usage.output_tokens or self._state["estimated_output_tokens"],
                tokens_per_second=round(tokens_per_second, 3),
                tokens_per_second_estimated=False,
                metric_source=source,
                comparison_cost_usd=round(comparison_cost_usd, 8),
                comparison_model=comparison_model,
                error=None,
            )

    def fail(self, message: str) -> None:
        with self._lock:
            self._state.update(active=False, phase="error", error=message[:300], updated_at=time.time())

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            value = dict(self._state)
        started = value.get("started_at")
        value["elapsed_seconds"] = round(max(now - float(started), 0.0), 3) if started else 0.0
        if value["active"] and value["estimated_output_tokens"]:
            generation_started = value.get("generation_started_at") or started or now
            value["tokens_per_second"] = round(
                value["estimated_output_tokens"] / max(now - float(generation_started), 0.001), 3
            )
            value["tokens_per_second_estimated"] = True
        first_token = value.get("generation_started_at")
        value["time_to_first_token_seconds"] = (
            round(max(float(first_token) - float(started), 0.0), 3)
            if first_token and started
            else None
        )
        return {
            "schema_version": 2,
            "active": value["active"],
            "phase": value["phase"],
            "model": value["model"],
            "turn_id": value["turn_id"],
            "session_id": value["session_id"],
            "started_at": value["started_at"],
            "updated_at": value["updated_at"],
            "elapsed_seconds": value["elapsed_seconds"],
            "estimated_output_tokens": value["estimated_output_tokens"],
            "estimated_reasoning_tokens": value["estimated_reasoning_tokens"],
            "input_tokens": value["input_tokens"],
            "output_tokens": value["output_tokens"],
            "tokens_per_second": value["tokens_per_second"],
            "tokens_per_second_estimated": value["tokens_per_second_estimated"],
            "thinking_summary": value["thinking_summary"],
            "last_tool": value["last_tool"],
            "comparison_cost_usd": value["comparison_cost_usd"],
            "comparison_model": value["comparison_model"],
            "error": value["error"],
            "turn": {
                "active": value["active"], "phase": value["phase"], "model": value["model"],
                "turn_id": value["turn_id"], "session_id": value["session_id"],
                "elapsed_seconds": value["elapsed_seconds"], "last_tool": value["last_tool"],
                "thinking_summary": value["thinking_summary"], "error": value["error"],
            },
            "tokens": {
                "input": value["input_tokens"], "output": value["output_tokens"],
                "estimated_output": value["estimated_output_tokens"],
                "estimated_visible_output": value["estimated_visible_tokens"],
                "estimated_reasoning": value["estimated_reasoning_tokens"],
                "exact": value["output_tokens"] is not None,
                "source": "ollama.responses.usage" if value["output_tokens"] is not None else "ollama.responses.stream",
            },
            "performance": {
                "tokens_per_second": value["tokens_per_second"],
                "estimated": value["tokens_per_second_estimated"],
                "time_to_first_token_seconds": value["time_to_first_token_seconds"],
                "source": value["metric_source"],
            },
            "cost": {
                "comparison_usd": value["comparison_cost_usd"],
                "comparison_model": value["comparison_model"],
            },
            "ollama_runtime": value["ollama_runtime"],
            "ollama_version": value["ollama_version"],
        }


class TelemetrySSEParser:
    """Incrementally observes useful public Responses stream events.

    Full stream data remains with the protocol transformer; this parser only
    watches summary and output text deltas for the side-channel dashboard.
    """

    def __init__(self, hub: TelemetryHub | None) -> None:
        self.hub = hub
        self._buffer = ""

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk.decode("utf-8", errors="replace")
        while True:
            separator = "\r\n\r\n" if "\r\n\r\n" in self._buffer else "\n\n"
            if separator not in self._buffer:
                return
            block, self._buffer = self._buffer.split(separator, 1)
            self._event(block)

    def finish(self) -> None:
        if self._buffer.strip():
            self._event(self._buffer)
        self._buffer = ""

    def _event(self, block: str) -> None:
        if self.hub is None:
            return
        data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if not data_lines:
            return
        try:
            event = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        event_type = str(event.get("type", "")).lower()
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "reasoning":
            self.hub.reasoning_item(item)
        if event_type in {"response.reasoning_summary_text.delta", "response.reasoning_summary.delta"}:
            self.hub.reasoning_summary_delta(str(event.get("delta", "")))
        elif event_type == "response.output_text.delta":
            self.hub.output_delta(str(event.get("delta", "")))
        elif "function_call" in event_type:
            self.hub.phase("tool")
