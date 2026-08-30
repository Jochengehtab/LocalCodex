from __future__ import annotations

import re
from typing import Any


WINDOWS_PATH = re.compile(
    r"(?<![A-Za-z0-9_./])([A-Za-z]):[\\/]+([^\s\"'<>|]+)"
)
# Codex options whose following value is a filesystem path.
PATH_OPTIONS = {"-C", "--cd", "--add-dir", "-i", "--image"}


def windows_to_wsl(value: str) -> str:
    """Translate common drive-letter paths while leaving normal text unchanged."""
    if re.match(r"^[A-Za-z]:[\\/]", value):
        rest = re.sub(r"^[A-Za-z]:[\\/]+", "", value)
        rest = re.sub(r"[\\/]+", "/", rest)
        return f"/mnt/{value[0].lower()}/{rest}"

    def replace(match: re.Match[str]) -> str:
        drive = match.group(1).lower()
        rest = re.sub(r"[\\/]+", "/", match.group(2))
        return f"/mnt/{drive}/{rest}"

    return WINDOWS_PATH.sub(replace, value)


def map_windows_paths(value: Any) -> Any:
    if isinstance(value, str):
        return windows_to_wsl(value)
    if isinstance(value, list):
        return [map_windows_paths(item) for item in value]
    if isinstance(value, dict):
        return {key: map_windows_paths(item) for key, item in value.items()}
    return value


def map_cli_paths(arguments: list[str]) -> list[str]:
    mapped: list[str] = []
    consume_path = False
    for argument in arguments:
        if consume_path:
            mapped.append(windows_to_wsl(argument))
            consume_path = False
            continue
        if argument in PATH_OPTIONS:
            mapped.append(argument)
            consume_path = True
        elif any(argument.startswith(f"{option}=") for option in PATH_OPTIONS):
            option, path = argument.split("=", 1)
            mapped.append(f"{option}={windows_to_wsl(path)}")
        else:
            mapped.append(argument)
    return mapped
