"""CLI dispatch separated from launcher process ownership."""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import json
import subprocess


@dataclass
class LauncherCommands:
    root: Path
    python: Path
    home: Path
    setup: Callable[..., None]
    context_arguments: Callable[[list[str]], list[str]]
    start_search: Callable[[], bool]
    search_doctor: Callable[[], int]
    monitor_doctor: Callable[[], int]
    doctor: Callable[[bool], int]
    self_test: Callable[[bool], int]


def dispatch(arguments: list[str], services: LauncherCommands) -> int | None:
    if not arguments:
        return None
    command, rest = arguments[0], arguments[1:]
    if command == "license":
        print((services.root / "LICENSE").read_text(encoding="utf-8"))
        return 0
    if command == "update":
        from .releases import perform_update

        return perform_update(rest[0] if rest else None)
    if command == "rollback":
        from .releases import rollback

        return rollback()
    if command == "uninstall":
        from .releases import uninstall

        return uninstall(purge_data="--purge-data" in rest)
    if command == "benchmark":
        return subprocess.run(
            [str(services.python), "-m", "local_codex.setup", "--benchmark", *rest],
            cwd=services.root,
            check=False,
        ).returncode
    if command in {"--setup", "setup"}:
        services.setup(
            benchmark="--benchmark" in rest,
            context_arguments=services.context_arguments(rest),
            build_monitor="--build-monitor" in rest,
            refresh_config="--refresh-config" in rest,
        )
        services.start_search()
        return 0
    if command == "--search-doctor":
        services.setup()
        return services.search_doctor()
    if command == "--monitor-doctor":
        return services.monitor_doctor()
    if command in {"--doctor", "doctor"}:
        return services.doctor("--json" in rest)
    if command in {"--self-test", "self-test"}:
        return services.self_test("--json" in rest)
    if command in {"--usage", "usage", "--usage-json"}:
        from .usage import UsageStore, format_summary

        services.setup()
        store = UsageStore(services.home / "state" / "usage.sqlite3")
        try:
            summary = store.summary(None if "--all" in rest else 30)
        finally:
            store.close()
        print(
            json.dumps(summary, ensure_ascii=False, indent=2)
            if command == "--usage-json"
            else format_summary(summary)
        )
        return 0
    return None
