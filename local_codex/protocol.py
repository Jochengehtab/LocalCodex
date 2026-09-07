from __future__ import annotations

import copy
import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from typing import Any, Iterable

from .paths import map_windows_paths


SHELL_ALIASES = {
    "bash",
    "shell",
    "terminal",
    "run_command",
    "mcp__workspace__bash",
    "mcp__workspace__shell",
}
PREFIXES = ("mcp__workspace__", "functions.", "tools.")
SEARCH_ALIASES = {
    "web_search": {"web_search", "search_web", "internet_search", "browser_search", "search"},
    "fetch_page": {"fetch_page", "open_url", "read_url", "fetch_url", "browse_url"},
}


def offered_function_names(tools: Any) -> set[str]:
    names: set[str] = set()
    if not isinstance(tools, list):
        return names
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if isinstance(name, str):
            names.add(name)
        for child in tool.get("tools", []) if isinstance(tool.get("tools"), list) else []:
            child_name = child.get("name") if isinstance(child, dict) else None
            if isinstance(child_name, str):
                names.add(child_name)
    return names


def normalize_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """Accept the tested Responses function contract without silently dropping tools."""
    result = []
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise ValueError("Unsupported tool schema; this adapter requires Responses function tools")
        name = tool.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Tool names must be nonempty and unique")
        names.add(name)
        result.append(copy.deepcopy(tool))
    return result


def _strip_prefix(name: str) -> str:
    for prefix in PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def canonical_tool_name(name: str, offered: set[str]) -> str:
    if name in offered:
        return name
    lowered = name.lower()
    if lowered in SHELL_ALIASES and "exec_command" in offered:
        return "exec_command"
    stripped = _strip_prefix(name)
    for candidate in offered:
        if candidate.lower() == stripped.lower():
            return candidate
    for canonical, aliases in SEARCH_ALIASES.items():
        if lowered in aliases or stripped.lower() in aliases:
            matches = [
                candidate
                for candidate in offered
                if candidate.lower() == canonical
                or candidate.lower().endswith(f"__{canonical}")
            ]
            if matches:
                return sorted(matches)[0]
    if lowered in {"apply_patch", "applypatch", "edit", "write"} and "exec_command" in offered:
        return "exec_command"
    raise ValueError(f"Unsupported model tool: {name}")


def _patch_command(patch: str) -> str:
    digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()[:12]
    marker = f"LOCAL_CODEX_PATCH_{digest}"
    while marker in patch:
        marker += "X"
    return f"apply_patch <<'{marker}'\n{patch}\n{marker}"


def canonical_arguments(original_name: str, target_name: str, arguments: Any) -> str:
    if isinstance(arguments, str):
        try:
            data = json.loads(arguments)
        except json.JSONDecodeError:
            data = {"command": arguments}
    elif isinstance(arguments, dict):
        data = copy.deepcopy(arguments)
    else:
        data = {}

    # Models may return Windows paths even though the tool process runs in WSL.
    # Normalize them before any tool-specific alias handling so every branch,
    # including view_image, receives a usable Linux path.
    data = map_windows_paths(data)

    lowered = original_name.lower()
    if target_name == "exec_command":
        if lowered in {"apply_patch", "applypatch", "edit", "write"}:
            patch = data.get("patch") or data.get("input") or data.get("content")
            if isinstance(patch, str) and "*** Begin Patch" in patch:
                data = {"cmd": _patch_command(patch)}
            else:
                message = f"Unsupported edit tool shape from model: {original_name}"
                data = {"cmd": f"printf '%s\\n' {shlex.quote(message)} >&2; exit 2"}
        else:
            if "cmd" not in data and "command" in data:
                data["cmd"] = data.pop("command")
            if "workdir" not in data and "cwd" in data:
                data["workdir"] = data.pop("cwd")
            if "cmd" not in data:
                message = f"Unsupported tool alias from model: {original_name}"
                data = {"cmd": f"printf '%s\\n' {shlex.quote(message)} >&2; exit 2"}
    elif target_name == "view_image" and "path" not in data and "file_path" in data:
        data["path"] = data.pop("file_path")
    elif target_name.lower().endswith("__web_search") or target_name == "web_search":
        if "query" not in data:
            for alias in ("q", "search_query", "term"):
                if alias in data:
                    data["query"] = data.pop(alias)
                    break
    elif target_name.lower().endswith("__fetch_page") or target_name == "fetch_page":
        if "url" not in data:
            for alias in ("uri", "link", "page_url"):
                if alias in data:
                    data["url"] = data.pop(alias)
                    break
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def normalize_function_call(item: dict[str, Any], offered: set[str]) -> dict[str, Any]:
    result = copy.deepcopy(item)
    original_name = str(result.get("name", ""))
    target_name = canonical_tool_name(original_name, offered)
    result["name"] = target_name
    if "arguments" in result:
        result["arguments"] = canonical_arguments(
            original_name, target_name, result.get("arguments")
        )
    return result


def normalize_response(response: dict[str, Any], offered: set[str]) -> dict[str, Any]:
    result = copy.deepcopy(response)
    output: list[dict[str, Any]] = []
    for item in result.get("output", []):
        if isinstance(item, dict) and item.get("type") == "reasoning":
            continue
        if isinstance(item, dict) and item.get("type") == "function_call":
            output.append(normalize_function_call(item, offered))
        else:
            output.append(item)
    result["output"] = output
    return result


def response_as_sse(response: dict[str, Any]) -> bytes:
    """Render a complete non-streaming response as the minimal SSE Codex accepts."""
    events: list[str] = []
    for index, item in enumerate(response.get("output", [])):
        data = {
            "type": "response.output_item.done",
            "output_index": index,
            "item": item,
        }
        events.append(
            "event: response.output_item.done\n"
            f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        )
    completed = {"type": "response.completed", "response": response}
    events.append(
        "event: response.completed\n"
        f"data: {json.dumps(completed, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )
    events.append("data: [DONE]\n\n")
    return "".join(events).encode()


@dataclass
class SSEEvent:
    event: str | None
    data: str


def parse_sse(payload: str) -> list[SSEEvent]:
    events: list[SSEEvent] = []
    for block in re.split(r"\r?\n\r?\n", payload.strip()):
        if not block:
            continue
        event_name: str | None = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            events.append(SSEEvent(event_name, "\n".join(data_lines)))
    return events


def transform_sse(payload: str, offered: set[str]) -> tuple[bytes, dict[str, Any] | None]:
    parsed = parse_sse(payload)
    call_targets: dict[str, tuple[str, str]] = {}
    completed_response: dict[str, Any] | None = None
    rendered: list[str] = []

    for event in parsed:
        if event.data == "[DONE]":
            rendered.append("data: [DONE]\n\n")
            continue
        try:
            data = json.loads(event.data)
        except json.JSONDecodeError:
            prefix = f"event: {event.event}\n" if event.event else ""
            rendered.append(f"{prefix}data: {event.data}\n\n")
            continue

        event_type = str(data.get("type") or event.event or "").lower()
        event_item = data.get("item")
        if "reasoning" in event_type or (
            isinstance(event_item, dict) and event_item.get("type") == "reasoning"
        ):
            # LocalCodex does not expose, store or feed reasoning summaries
            # back into Codex. Exact output usage remains in response.completed.
            continue

        item = data.get("item")
        if isinstance(item, dict) and item.get("type") == "function_call":
            original = str(item.get("name", ""))
            target = canonical_tool_name(original, offered)
            item_id = str(item.get("id", ""))
            call_targets[item_id] = (original, target)
            data["item"] = normalize_function_call(item, offered)

        item_id = str(data.get("item_id", ""))
        if item_id in call_targets and "arguments" in data:
            original, target = call_targets[item_id]
            data["arguments"] = canonical_arguments(original, target, data["arguments"])
        if item_id in call_targets and "delta" in data:
            delta = data.get("delta")
            if isinstance(delta, str):
                data["delta"] = re.sub(r'"command"\s*:', '"cmd":', delta)
                data["delta"] = re.sub(r'"cwd"\s*:', '"workdir":', data["delta"])

        response = data.get("response")
        if isinstance(response, dict):
            normalized = normalize_response(response, offered)
            data["response"] = normalized
            if data.get("type") == "response.completed":
                completed_response = normalized

        prefix = f"event: {event.event}\n" if event.event else ""
        rendered.append(
            f"{prefix}data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        )

    return "".join(rendered).encode("utf-8"), completed_response


def filter_input(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve host instructions; reasoning must not be persisted or replayed."""
    return [copy.deepcopy(item) for item in items if item.get("type") != "reasoning"]
