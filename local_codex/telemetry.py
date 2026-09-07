"""Small in-memory telemetry surface for the optional native monitor.

Reasoning content is intentionally neither parsed nor retained.  This module has
no disk writes and is kept independent from inference so a closed monitor cannot
slow down a turn.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .usage import TokenUsage, estimate_cost


class TelemetryHub:
    """Thread-safe, bounded status for exactly one serialized local inference."""

    def __init__(
        self,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._lock = threading.Lock()
        self._state: dict[str, Any] = self._idle_state()
        self._revision = 0
        self._rate_samples: deque[tuple[float, int]] = deque()

    def _touch_locked(self) -> None:
        self._revision += 1
        self._state["updated_at"] = self._wall_clock()

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def active(self) -> bool:
        with self._lock:
            return bool(self._state.get("active"))

    def _idle_state(self) -> dict[str, Any]:
        return {
            "active": False,
            "phase": "idle",
            "model": None,
            "turn_id": None,
            "session_id": None,
            "started_at": None,
            "generation_started_at": None,
            "updated_at": self._wall_clock(),
            "estimated_output_tokens": 0,
            "estimated_visible_tokens": 0,
            "visible_output_characters": 0,
            "input_tokens": None,
            "output_tokens": None,
            "tokens_per_second": None,
            "average_tokens_per_second": None,
            "tokens_per_second_estimated": False,
            "mode": None,
            "source_model": None,
            "route_reason": None,
            "last_tool": None,
            "comparison_cost_usd": None,
            "comparison_model": None,
            "error": None,
            "metric_source": "ollama.responses.stream",
            "ollama_runtime": {},
            "ollama_version": None,
            "all_statistics": {},
        }

    def start(
        self,
        *,
        model: str,
        session_id: str,
        turn_id: str,
        session_statistics: dict[str, Any] | None = None,
        input_tokens_estimate: int | None = None,
        comparison_model: str | None = None,
        mode: str | None = None,
        source_model: str | None = None,
        route_reason: str | None = None,
    ) -> None:
        now = self._wall_clock()
        with self._lock:
            runtime = self._state.get("ollama_runtime", {})
            version = self._state.get("ollama_version")
            all_statistics = self._state.get("all_statistics", {})
            self._state = self._idle_state()
            self._rate_samples.clear()
            self._state.update(
                active=True,
                phase="loading",
                model=model,
                session_id=session_id,
                turn_id=turn_id,
                started_at=now,
                updated_at=now,
                session_statistics=dict(session_statistics or {}),
                ollama_runtime=runtime,
                ollama_version=version,
                all_statistics=all_statistics,
                input_tokens=input_tokens_estimate,
                comparison_model=comparison_model,
                mode=mode,
                source_model=source_model,
                route_reason=route_reason,
            )
            self._touch_locked()

    def update_session_statistics(self, session_id: str, statistics: dict[str, Any]) -> None:
        with self._lock:
            if self._state.get("session_id") == session_id:
                self._state["session_statistics"] = dict(statistics)
                self._touch_locked()

    def update_all_statistics(self, statistics: dict[str, Any]) -> None:
        with self._lock:
            self._state["all_statistics"] = dict(statistics)
            self._touch_locked()

    def phase(self, value: str, *, tool: str | None = None) -> None:
        with self._lock:
            self._state["phase"] = value
            if tool:
                self._state["last_tool"] = tool
            self._touch_locked()

    def output_delta(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            now = self._wall_clock()
            monotonic_now = self._monotonic_clock()
            self._state["phase"] = "generating"
            self._state["visible_output_characters"] += len(text)
            # Estimate from the complete stream so tiny deltas do not each
            # round up to a full token.
            estimate = math.ceil(self._state["visible_output_characters"] / 4)
            self._state["estimated_output_tokens"] = estimate
            self._state["estimated_visible_tokens"] = estimate
            self._state["generation_started_at"] = self._state["generation_started_at"] or now
            self._rate_samples.append((monotonic_now, estimate))
            cutoff = monotonic_now - 2.0
            while len(self._rate_samples) > 1 and self._rate_samples[1][0] <= cutoff:
                self._rate_samples.popleft()
            oldest_time, oldest_tokens = self._rate_samples[0]
            elapsed = monotonic_now - oldest_time
            if elapsed > 0:
                self._state["tokens_per_second"] = round(
                    max(0, estimate - oldest_tokens) / elapsed, 3
                )
            generation_elapsed = max(now - self._state["generation_started_at"], 0.001)
            self._state["average_tokens_per_second"] = round(estimate / generation_elapsed, 3)
            self._state["tokens_per_second_estimated"] = True
            self._touch_locked()

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
            next_version = version or self._state.get("ollama_version")
            if runtime != self._state.get("ollama_runtime") or next_version != self._state.get("ollama_version"):
                self._state["ollama_runtime"] = runtime
                self._state["ollama_version"] = next_version
                self._touch_locked()

    def complete(
        self,
        usage: TokenUsage,
        *,
        comparison_cost_usd: float,
        comparison_model: str,
        native_timing: dict[str, Any] | None = None,
    ) -> None:
        now = self._wall_clock()
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
                average_tokens_per_second=round(tokens_per_second, 3),
                tokens_per_second_estimated=False,
                metric_source=source,
                comparison_cost_usd=round(comparison_cost_usd, 8),
                comparison_model=comparison_model,
                error=None,
            )
            self._touch_locked()

    def fail(self, message: str) -> None:
        with self._lock:
            self._state.update(active=False, phase="error", error=message[:300], updated_at=self._wall_clock())
            self._touch_locked()

    def snapshot(self) -> dict[str, Any]:
        now = self._wall_clock()
        with self._lock:
            value = dict(self._state)
            revision = self._revision
        started = value.get("started_at")
        value["elapsed_seconds"] = round(max(now - float(started), 0.0), 3) if started else 0.0
        first_token = value.get("generation_started_at")
        value["time_to_first_token_seconds"] = (
            round(max(float(first_token) - float(started), 0.0), 3)
            if first_token and started
            else None
        )
        live_cost = (
            estimate_cost(
                TokenUsage(
                    input_tokens=int(value.get("input_tokens") or 0),
                    output_tokens=int(value.get("estimated_output_tokens") or 0),
                ),
                str(value.get("comparison_model") or ""),
            )
            if value["active"] else 0.0
        )
        session_statistics = value.get("session_statistics", {})
        all_statistics = value.get("all_statistics", {})
        return {
            "schema_version": 5,
            "sequence": revision,
            "sampled_at": now,
            "active": value["active"],
            "phase": value["phase"],
            "model": value["model"],
            "turn_id": value["turn_id"],
            "session_id": value["session_id"],
            "started_at": value["started_at"],
            "updated_at": value["updated_at"],
            "elapsed_seconds": value["elapsed_seconds"],
            "estimated_output_tokens": value["estimated_output_tokens"],
            "input_tokens": value["input_tokens"],
            "output_tokens": value["output_tokens"],
            "tokens_per_second": value["tokens_per_second"],
            "tokens_per_second_estimated": value["tokens_per_second_estimated"],
            "last_tool": value["last_tool"],
            "comparison_cost_usd": value["comparison_cost_usd"],
            "comparison_model": value["comparison_model"],
            "error": value["error"],
            "turn": {
                "active": value["active"], "phase": value["phase"], "model": value["model"],
                "mode": value.get("mode") or (
                    "vision" if "vision" in str(value["model"]) else
                    "plan" if "plan" in str(value["model"]) else "build"
                ),
                "role": value.get("mode") or (
                    "vision" if "vision" in str(value["model"]) else
                    "plan" if "plan" in str(value["model"]) else "build"
                ),
                "source_model": value.get("source_model"),
                "route_reason": value.get("route_reason"),
                "turn_id": value["turn_id"], "session_id": value["session_id"],
                "elapsed_seconds": value["elapsed_seconds"], "last_tool": value["last_tool"],
                "error": value["error"],
            },
            "tokens": {
                "input": value["input_tokens"], "output": value["output_tokens"],
                "estimated_output": value["estimated_output_tokens"],
                "estimated_visible_output": value["estimated_visible_tokens"],
                "exact": value["output_tokens"] is not None,
                "source": "ollama.responses.usage" if value["output_tokens"] is not None else "ollama.responses.stream",
            },
            "performance": {
                "tokens_per_second": value["tokens_per_second"],
                "average_tokens_per_second": value["average_tokens_per_second"],
                "estimated": value["tokens_per_second_estimated"],
                "time_to_first_token_seconds": value["time_to_first_token_seconds"],
                "source": value["metric_source"],
            },
            "context": {
                "used_tokens": int(value.get("input_tokens") or 0) + int(value.get("estimated_output_tokens") or 0),
                "capacity_tokens": int(value.get("ollama_runtime", {}).get("context_length") or 0),
                "estimated": value.get("input_tokens") is None or value.get("output_tokens") is None,
            },
            "cost": {
                "comparison_usd": value["comparison_cost_usd"],
                "comparison_model": value["comparison_model"],
            },
            "ollama_runtime": value["ollama_runtime"],
            "ollama_version": value["ollama_version"],
            "session": {
                **session_statistics,
                "live_input_tokens": (
                    int(session_statistics.get("input_tokens", 0)) + int(value.get("input_tokens") or 0)
                    if value["active"] else int(session_statistics.get("input_tokens", 0))
                ),
                "live_output_tokens": (
                    int(session_statistics.get("output_tokens", 0))
                    + int(value["estimated_output_tokens"])
                    if value["active"] else
                    int(session_statistics.get("output_tokens", 0))
                ),
                "live_saved_usd": round(
                    float(session_statistics.get("estimated_saved_usd", 0.0)) + live_cost, 8
                ),
            },
            "total": {
                **all_statistics,
                "live_input_tokens": (
                    int(all_statistics.get("input_tokens", 0)) + int(value.get("input_tokens") or 0)
                    if value["active"] else int(all_statistics.get("input_tokens", 0))
                ),
                "live_output_tokens": (
                    int(all_statistics.get("output_tokens", 0))
                    + int(value["estimated_output_tokens"])
                    if value["active"] else
                    int(all_statistics.get("output_tokens", 0))
                ),
                "live_saved_usd": round(
                    float(all_statistics.get("estimated_saved_usd", 0.0)) + live_cost, 8
                ),
            },
        }


class TelemetrySSEParser:
    """Incrementally observes useful public Responses stream events.

    Full stream data remains with the protocol transformer; this parser only
    watches only output text and tool deltas for the side-channel dashboard.
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
        if event_type == "response.output_text.delta":
            self.hub.output_delta(str(event.get("delta", "")))
        elif "function_call" in event_type:
            self.hub.phase("tool")
