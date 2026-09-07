"""Small structural interfaces for replaceable persistence and telemetry adapters."""

from __future__ import annotations
from typing import Any, Protocol
from .usage import TokenUsage


class ResponseStore(Protocol):
    def expand(
        self, previous_response_id: str | None, current_input: Any, *, session_id: str | None = None
    ) -> list[dict[str, Any]]: ...
    def put(
        self,
        response_id: str,
        session_id: str,
        turn_id: str,
        full_input: list[dict[str, Any]],
        output: list[dict[str, Any]],
    ) -> None: ...
    def close(self) -> None: ...


class UsageRepository(Protocol):
    def record(
        self,
        *,
        session_id: str,
        turn_id: str,
        model: str,
        usage: TokenUsage,
        comparison_model: str,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...
    def statistics(
        self, *, period: str = "30d", session_id: str | None = None, page: int = 1, page_size: int = 50
    ) -> dict[str, Any]: ...
    def summary(self, days: int | None = 30) -> dict[str, Any]: ...
    def export(self, *, period: str = "30d", session_id: str | None = None, format: str = "json") -> str: ...
    def set_session_title(self, session_id: str, title: str) -> None: ...
    def close(self) -> None: ...
