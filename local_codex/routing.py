from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Mapping

from .settings import RuntimeSettings, SETTINGS


ERROR_PATTERN = re.compile(
    r"\b(traceback|exception|syntaxerror|typeerror|segfault|core dumped|"
    r"command failed|test[s]? failed|exit (?:code|status)\s*[:=]?\s*[1-9]|"
    r"fehlgeschlagen|fehler)\b",
    re.IGNORECASE,
)
PLAN_PATTERN = re.compile(
    r"(<collaboration_mode>.*?plan|\bplan mode\b|\bcode review\b|"
    r"\breview the (?:code|diff|changes)\b|"
    r"\b(?:create|make|write|draft) (?:a )?plan\b|"
    r"\b(?:erstelle|schreibe|mache) (?:mir )?(?:einen )?plan\b|"
    r"/plan\b|/review\b)",
    re.IGNORECASE | re.DOTALL,
)
IMAGE_TYPES = {"input_image", "image", "local_image", "image_url"}


def _walk(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def contains_image(value: Any) -> bool:
    for item in _walk(value):
        item_type = str(item.get("type", "")).lower()
        if item_type in IMAGE_TYPES or "image_url" in item or "images" in item:
            return True
    return False


def text_from(value: Any) -> str:
    chunks: list[str] = []
    for item in _walk(value):
        for key in ("text", "output"):
            content = item.get(key)
            if isinstance(content, str):
                chunks.append(content)
    if isinstance(value, str):
        chunks.append(value)
    return "\n".join(chunks)


def latest_user_text(value: Any) -> str:
    """Return the latest real user message without host/developer instructions."""
    if not isinstance(value, list):
        return text_from(value)
    messages = [
        item
        for item in value
        if isinstance(item, Mapping)
        and item.get("type") == "message"
        and item.get("role") == "user"
    ]
    return text_from(messages[-1]) if messages else text_from(value)


def has_tool_error(value: Any) -> bool:
    tool_outputs: list[Any] = []
    for item in _walk(value):
        if item.get("type") in {
            "function_call_output",
            "custom_tool_call_output",
            "computer_tool_call_output",
        }:
            tool_outputs.append(item)
    return bool(tool_outputs) and bool(ERROR_PATTERN.search(text_from(tool_outputs)))


def turn_metadata(headers: Mapping[str, str], body: Mapping[str, Any]) -> dict[str, Any]:
    raw = headers.get("x-codex-turn-metadata") or headers.get("X-Codex-Turn-Metadata")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    metadata = body.get("client_metadata")
    return metadata if isinstance(metadata, dict) else {}


@dataclass(frozen=True)
class RouteDecision:
    model: str
    reason: str
    turn_id: str
    session_id: str


class TurnRouter:
    def __init__(self, settings: RuntimeSettings = SETTINGS, max_turns: int = 256):
        self.settings = settings
        self.max_turns = max_turns
        self._turns: OrderedDict[tuple[str, str], tuple[str, float]] = OrderedDict()
        self._lock = threading.Lock()

    def choose(self, body: Mapping[str, Any], headers: Mapping[str, str]) -> RouteDecision:
        metadata = turn_metadata(headers, body)
        session_value = (
            metadata.get("session_id")
            or headers.get("session-id")
            or body.get("prompt_cache_key")
        )
        if not session_value:
            # Older Codex builds do not always send thread metadata. Never
            # merge every such invocation into one "unknown-session" bucket.
            session_value = headers.get("x-client-request-id") or f"local-{uuid.uuid4()}"
        session_id = str(session_value)
        turn_id = str(metadata.get("turn_id") or headers.get("x-client-request-id") or session_id)
        current_input = body.get("input", [])
        request_text = latest_user_text(current_input)
        turn_key = (session_id, turn_id)

        with self._lock:
            existing = self._turns.get(turn_key)
            if contains_image(current_input):
                model, reason = self.settings.vision_model, "image input"
            elif has_tool_error(current_input):
                model, reason = self.settings.plan_model, "tool error recovery"
            elif existing:
                model, reason = existing[0], "same Codex turn"
            elif PLAN_PATTERN.search(request_text):
                model, reason = self.settings.plan_model, "plan or review"
            else:
                model, reason = self.settings.build_model, "implementation default"

            self._turns[turn_key] = (model, time.time())
            self._turns.move_to_end(turn_key)
            while len(self._turns) > self.max_turns:
                self._turns.popitem(last=False)

        return RouteDecision(model=model, reason=reason, turn_id=turn_id, session_id=session_id)
