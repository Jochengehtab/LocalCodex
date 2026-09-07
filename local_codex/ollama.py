"""Bounded, cancellation-safe Ollama transport adapter."""

from __future__ import annotations
from typing import Any, Callable
import httpx


class ResponseLimitError(ValueError):
    pass


async def buffered_response(
    client: httpx.AsyncClient,
    base_url: str,
    body: dict[str, Any],
    max_bytes: int,
    observe: Callable[[bytes], None],
) -> httpx.Response:
    outgoing = client.build_request("POST", f"{base_url}/v1/responses", json=body)
    response = await client.send(outgoing, stream=True)
    chunks: list[bytes] = []
    size = 0
    try:
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise ResponseLimitError("Model response exceeds the configured buffer limit")
            chunks.append(chunk)
            if response.status_code < 400:
                observe(chunk)
    finally:
        await response.aclose()
    # aiter_bytes has already decoded HTTP compression; do not carry the original
    # Content-Encoding into the new in-memory response and decode it a second time.
    return httpx.Response(response.status_code, content=b"".join(chunks))
