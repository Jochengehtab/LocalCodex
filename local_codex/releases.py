from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .settings import LOCAL_HOME, ROOT


REPOSITORY = "Jochengehtab/LocalCodex"
CURRENT_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
CHECK_INTERVAL_SECONDS = 24 * 60 * 60


def _version_tuple(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lstrip("v").split("-", 1)[0]
    try:
        return tuple(int(part) for part in cleaned.split("."))
    except ValueError:
        return (0,)


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    url: str


def latest_release(timeout: float = 1.5) -> ReleaseInfo | None:
    try:
        response = httpx.get(
            f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "LocalCodex"},
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        tag = str(payload.get("tag_name", "")).lstrip("v")
        if not tag or bool(payload.get("draft")) or bool(payload.get("prerelease")):
            return None
        return ReleaseInfo(tag, str(payload.get("html_url", "")))
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def _state_path() -> Path:
    return LOCAL_HOME / "update-state.json"


def _read_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _write_state(value: dict) -> None:
    try:
        _state_path().parent.mkdir(parents=True, exist_ok=True)
        _state_path().write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def installed_release() -> bool:
    return bool(os.environ.get("LOCAL_CODEX_INSTALL_ROOT"))


def perform_update(version: str | None = None) -> int:
    info = None if version else latest_release(timeout=10.0)
    target = version or (info.version if info else None)
    if not target:
        print("[local-codex] Kein stabiles GitHub-Release gefunden.", file=sys.stderr)
        return 1
    if os.environ.get("WSL_DISTRO_NAME") and shutil.which("powershell.exe"):
        script = subprocess.run(
            ["wslpath", "-w", str(ROOT / "install.ps1")], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        ).stdout.strip()
        if script:
            return subprocess.run([
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", script, "-Version", target, "-Yes",
            ], check=False).returncode
    return subprocess.run(
        ["bash", str(ROOT / "install.sh"), "--version", target, "--yes", "--update"],
        check=False,
    ).returncode


def rollback() -> int:
    root_value = os.environ.get("LOCAL_CODEX_INSTALL_ROOT")
    if not root_value:
        print("[local-codex] Rollback ist nur für eine Release-Installation verfügbar.", file=sys.stderr)
        return 1
    versions = Path(root_value) / "versions"
    candidates = sorted(
        (path.name.lstrip("v") for path in versions.glob("v*") if path.is_dir()),
        key=_version_tuple,
        reverse=True,
    )
    previous = next((value for value in candidates if _version_tuple(value) < _version_tuple(CURRENT_VERSION)), None)
    if not previous:
        print("[local-codex] Keine ältere installierte Version gefunden.", file=sys.stderr)
        return 1
    return perform_update(previous)


def uninstall(*, purge_data: bool = False) -> int:
    root_value = os.environ.get("LOCAL_CODEX_INSTALL_ROOT")
    if not root_value:
        print("[local-codex] Uninstall ist nur für eine Release-Installation verfügbar.", file=sys.stderr)
        return 1
    root = Path(root_value).expanduser().resolve()
    if root in {Path.home(), Path("/"), Path.home().parent}:
        print("[local-codex] Unsicheres Installationsziel; Abbruch.", file=sys.stderr)
        return 1
    if sys.stdin.isatty():
        answer = input(f"LocalCodex-Programme aus {root} entfernen? [j/N] ").strip().lower()
        if answer not in {"j", "ja", "y", "yes"}:
            return 1
    for name in ("current", "previous"):
        path = root / name
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
    shutil.rmtree(root / "versions", ignore_errors=True)
    wrapper = Path.home() / ".local" / "bin" / "codex-local"
    wrapper.unlink(missing_ok=True)
    if purge_data:
        shutil.rmtree(root / "data", ignore_errors=True)
    if os.environ.get("WSL_DISTRO_NAME") and shutil.which("powershell.exe"):
        subprocess.run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'LocalCodex') -Recurse -Force -ErrorAction SilentlyContinue",
        ], check=False)
    print("[local-codex] Programme entfernt." + (" Statistiken gelöscht." if purge_data else " Statistiken bleiben erhalten."))
    return 0


def maybe_prompt_for_update() -> bool:
    """Check at most daily; return True when an update was installed."""
    if not installed_release() or not sys.stdin.isatty() or os.environ.get("LOCAL_CODEX_NO_UPDATE_CHECK") == "1":
        return False
    state = _read_state()
    now = time.time()
    if now - float(state.get("last_check", 0)) < CHECK_INTERVAL_SECONDS:
        return False
    info = latest_release()
    state["last_check"] = now
    _write_state(state)
    if not info or _version_tuple(info.version) <= _version_tuple(CURRENT_VERSION):
        return False
    if state.get("ignored_version") == info.version:
        return False
    print(f"[local-codex] Neue Version {info.version} verfügbar (installiert: {CURRENT_VERSION}).")
    try:
        choice = input("Jetzt aktualisieren [j], diesmal überspringen [Enter], Version ignorieren [i]? ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if choice in {"i", "ignore"}:
        state["ignored_version"] = info.version
        _write_state(state)
        return False
    if choice not in {"j", "ja", "y", "yes"}:
        return False
    if perform_update(info.version) == 0:
        print("[local-codex] Update installiert. Bitte LocalCodex erneut starten.")
        return True
    print("[local-codex] Update fehlgeschlagen; die aktive Version wurde nicht verändert.", file=sys.stderr)
    return False
