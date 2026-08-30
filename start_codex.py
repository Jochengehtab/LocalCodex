#!/usr/bin/env python3

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path, PureWindowsPath

import httpx

from local_codex.diagnostics import (
    DiagnosticReport,
    LocalArgumentError,
    forced_local_codex_command,
    format_report,
    run_preflight,
    validate_local_arguments,
)
from local_codex.paths import map_cli_paths
from local_codex.settings import SETTINGS


ROOT = Path(__file__).resolve().parent
LOCAL_HOME = ROOT / ".codex-local"
PYTHON = ROOT / ".venv" / "bin" / "python"
HEALTH_URL = "http://127.0.0.1:18081/health"
ROUTER_URL = "http://127.0.0.1:18081"
SEARCH_URL = "http://127.0.0.1:18082"
SEARCH_COMPOSE = ROOT / "local_search" / "docker-compose.yml"
SEARCH_ENV = LOCAL_HOME / "search.env"
ROUTER_LOG = LOCAL_HOME / "state" / "router.log"
MONITOR_SHUTDOWN_EVENT = r"Local\LocalCodexMonitorShutdown"


def local_environment() -> dict[str, str]:
    return {
        **os.environ,
        "CODEX_HOME": str(LOCAL_HOME),
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }


def local_preflight() -> DiagnosticReport:
    return run_preflight(
        local_home=LOCAL_HOME,
        ollama_base_url=SETTINGS.ollama_base_url,
        router_base_url=ROUTER_URL,
        required_models=(SETTINGS.plan_model, SETTINGS.build_model, SETTINGS.vision_model),
    )


def print_report(report: DiagnosticReport, json_output: bool = False) -> None:
    if json_output:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))


def confirm_failed_preflight(force: bool) -> bool:
    if force:
        print("[local-codex] Preflight wird durch --force übergangen; der Provider bleibt lokal.", file=sys.stderr)
        return True
    if not sys.stdin.isatty():
        print("[local-codex] Abbruch: Preflight fehlgeschlagen und kein interaktives Terminal vorhanden.", file=sys.stderr)
        return False
    try:
        answer = input("Lokalen Codex trotz fehlgeschlagener Prüfung starten? [j/N] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in {"j", "ja", "y", "yes"}


def router_ready() -> bool:
    try:
        response = httpx.get(HEALTH_URL, timeout=1.0)
        return response.status_code == 200 and response.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False


def start_router() -> subprocess.Popen[str] | None:
    if router_ready():
        return None
    ROUTER_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = ROUTER_LOG.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [str(PYTHON), "-m", "local_codex.app"], cwd=ROOT, text=True,
        env={**os.environ, "LOCAL_CODEX_MANAGED": "1"},
        stdin=subprocess.DEVNULL,
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    log.close()
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("Der lokale Codex-Router wurde unerwartet beendet")
        if router_ready():
            return process
        time.sleep(0.25)
    process.terminate()
    raise RuntimeError("Der lokale Codex-Router wurde nicht rechtzeitig bereit")


def _monitor_runtime() -> dict:
    try:
        import json

        value = json.loads((LOCAL_HOME / "runtime.json").read_text(encoding="utf-8"))
        monitor = value.get("monitor")
        return monitor if isinstance(monitor, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def start_monitor(enabled: bool) -> subprocess.Popen[str] | None:
    """Launch the installed native singleton monitor."""
    if not enabled:
        return None
    runtime = _monitor_runtime()
    executable = runtime.get("wsl_executable")
    if not isinstance(executable, str) or not Path(executable).exists():
        print("Hinweis: Windows-Monitor ist nicht installiert; `codex-local --setup` repariert ihn.")
        return None
    try:
        process = subprocess.Popen(
            [executable, "--router-url", "http://127.0.0.1:18081"],
            cwd=ROOT, text=True, start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.4)
        if process.poll() not in (None, 0):
            print(f"Hinweis: Windows-Monitor wurde beendet. Log: {runtime.get('windows_log_file', '-')}")
        return process
    except OSError as exc:
        print(f"Hinweis: Windows-Monitor konnte nicht gestartet werden: {exc}")
        return None


def register_lease() -> str | None:
    lease_id = str(uuid.uuid4())
    try:
        response = httpx.post(
            f"http://127.0.0.1:18081/monitor/leases/{lease_id}",
            json={"process_id": os.getpid()}, timeout=3.0,
        )
        response.raise_for_status()
        return lease_id
    except httpx.HTTPError as exc:
        print(f"Warnung: Monitor-Sitzung konnte nicht registriert werden: {exc}", file=sys.stderr)
        return None


def lease_heartbeat(lease_id: str, stop: threading.Event) -> None:
    while not stop.wait(5.0):
        try:
            httpx.put(
                f"http://127.0.0.1:18081/monitor/leases/{lease_id}", timeout=2.0
            ).raise_for_status()
        except httpx.HTTPError:
            return


def release_lease(lease_id: str | None) -> None:
    if not lease_id:
        return
    try:
        httpx.delete(
            f"http://127.0.0.1:18081/monitor/leases/{lease_id}", timeout=2.0
        )
    except httpx.HTTPError:
        pass


def active_launcher_count() -> int | None:
    """Return the current shared-router lease count, or None if unavailable."""
    try:
        response = httpx.get(f"{ROUTER_URL}/monitor/snapshot", timeout=2.0)
        response.raise_for_status()
        value = response.json().get("sessions", {}).get("active_count")
        return int(value) if isinstance(value, (int, float)) else None
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None


def request_monitor_shutdown() -> bool:
    """Wake the native monitor so it can close without waiting for polling."""
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return False
    script = (
        "$e=[Threading.EventWaitHandle]::OpenExisting('"
        + MONITOR_SHUTDOWN_EVENT
        + "'); $e.Set() | Out-Null"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def stop_monitor_when_idle() -> None:
    """Close the shared tray monitor only after the last Codex lease is gone."""
    if active_launcher_count() == 0:
        request_monitor_shutdown()


def search_ready() -> bool:
    try:
        response = httpx.get(f"{SEARCH_URL}/", timeout=2.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def ensure_search_secret() -> None:
    LOCAL_HOME.mkdir(parents=True, exist_ok=True)
    if not SEARCH_ENV.exists():
        SEARCH_ENV.write_text(f"SEARXNG_SECRET={secrets.token_hex(32)}\n", encoding="utf-8")
        SEARCH_ENV.chmod(0o600)


def start_search() -> bool:
    if search_ready():
        return True
    if not shutil.which("docker"):
        print("Warnung: Docker fehlt; lokale Websuche bleibt deaktiviert.", file=sys.stderr)
        return False
    ensure_search_secret()
    command = ["docker", "compose"]
    if SEARCH_ENV.exists():
        command.extend(["--env-file", str(SEARCH_ENV)])
    command.extend(["-f", str(SEARCH_COMPOSE), "up", "-d"])
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except subprocess.CalledProcessError:
        print("Warnung: SearXNG konnte nicht gestartet werden; Codex startet ohne Websuche.", file=sys.stderr)
        return False
    for _ in range(60):
        if search_ready():
            return True
        time.sleep(0.5)
    print("Warnung: SearXNG ist noch nicht bereit; Codex startet trotzdem.", file=sys.stderr)
    return False


def search_doctor() -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Docker", shutil.which("docker") is not None, shutil.which("docker") or "nicht gefunden"))
    running = start_search()
    checks.append(("SearXNG", running, SEARCH_URL if running else "nicht erreichbar"))
    api_ok = False
    api_detail = "übersprungen"
    if running:
        try:
            response = httpx.post(
                f"{SEARCH_URL}/search",
                data={"q": "SearXNG", "format": "json", "categories": "general"},
                timeout=20.0,
            )
            response.raise_for_status()
            count = len(response.json().get("results", []))
            api_ok = count > 0
            api_detail = f"{count} Treffer"
        except (httpx.HTTPError, ValueError) as exc:
            api_detail = str(exc)
    checks.append(("Such-API", api_ok, api_detail))
    try:
        from local_search.core import WebAccessError, validate_public_url

        try:
            validate_public_url("http://127.0.0.1/private")
            ssrf_ok = False
        except WebAccessError:
            ssrf_ok = True
    except ImportError as exc:
        ssrf_ok = False
        api_detail = str(exc)
    checks.append(("SSRF-Schutz", ssrf_ok, "localhost wird blockiert" if ssrf_ok else api_detail))
    environment = {**os.environ, "CODEX_HOME": str(LOCAL_HOME)}
    mcp = subprocess.run(
        ["codex", "mcp", "list"], env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    mcp_ok = mcp.returncode == 0 and "local_search" in mcp.stdout
    checks.append(("Codex MCP", mcp_ok, "local_search registriert" if mcp_ok else mcp.stdout.strip()))
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else 'FEHLER'}] {name}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def monitor_doctor() -> int:
    ensure_setup()
    proxy = start_router()
    lease_id = register_lease()
    runtime = _monitor_runtime()
    executable = runtime.get("wsl_executable")
    diagnostic_windows = runtime.get("windows_ready_file")
    diagnostic_wsl = runtime.get("wsl_ready_file")
    if isinstance(diagnostic_windows, str):
        diagnostic_windows = str(PureWindowsPath(diagnostic_windows).with_name("monitor.diagnostic.json"))
    if isinstance(diagnostic_wsl, str):
        diagnostic_wsl = str(Path(diagnostic_wsl).with_name("monitor.diagnostic.json"))
    checks: list[tuple[str, bool, str]] = []
    checks.append(("WPF-EXE", isinstance(executable, str) and Path(executable).exists(), str(executable or "nicht installiert")))
    checks.append(("Router", router_ready(), HEALTH_URL))
    process = None
    if checks[0][1] and isinstance(diagnostic_windows, str):
        try:
            process = subprocess.run(
                [executable, "--router-url", "http://127.0.0.1:18081", "--diagnostic-file", diagnostic_windows],
                cwd=ROOT, timeout=15, check=False,
            )
            detail = "Diagnose erfolgreich" if process.returncode == 0 else f"Exit {process.returncode}"
            checks.append(("Windows→WSL", process.returncode == 0, detail))
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks.append(("Windows→WSL", False, str(exc)))
    else:
        checks.append(("Windows→WSL", False, "Monitor-EXE oder Diagnosepfad fehlt"))
    if isinstance(diagnostic_wsl, str) and Path(diagnostic_wsl).exists():
        print(Path(diagnostic_wsl).read_text(encoding="utf-8"))
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else 'FEHLER'}] {name}: {detail}")
    release_lease(lease_id)
    if proxy is not None:
        proxy.terminate()
        try:
            proxy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proxy.kill()
    return 0 if all(ok for _, ok, _ in checks) else 1


def local_doctor(json_output: bool = False) -> int:
    ensure_setup()
    proxy = None
    lease_id = None
    report = DiagnosticReport()
    try:
        try:
            proxy = start_router()
            lease_id = register_lease()
        except RuntimeError as exc:
            report.add("Router-Start", False, str(exc))
        report.extend(local_preflight().checks)
    finally:
        release_lease(lease_id)
        if proxy is not None:
            proxy.terminate()
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.kill()
    print_report(report, json_output)
    return 0 if report.ok else 1


def _contains_text(value, expected: str) -> bool:
    if isinstance(value, str):
        return expected in value
    if isinstance(value, dict):
        return any(_contains_text(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_contains_text(child, expected) for child in value)
    return False


def _ready_timestamp() -> int | None:
    ready = _monitor_runtime().get("wsl_ready_file")
    try:
        return Path(ready).stat().st_mtime_ns if isinstance(ready, str) else None
    except OSError:
        return None


def _wait_for_monitor_ready(previous: int | None, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = _ready_timestamp()
        if current is not None and (previous is None or current > previous):
            return True
        time.sleep(0.25)
    return False


def local_self_test(json_output: bool = False) -> int:
    ensure_setup()
    report = DiagnosticReport()
    proxy = None
    lease_id = None
    heartbeat_stop = threading.Event()
    heartbeat = None
    monitor_previous = _ready_timestamp()
    monitor_started = False
    try:
        try:
            proxy = start_router()
            lease_id = register_lease()
            if lease_id:
                heartbeat = threading.Thread(
                    target=lease_heartbeat, args=(lease_id, heartbeat_stop), daemon=True
                )
                heartbeat.start()
        except RuntimeError as exc:
            report.add("Router-Start", False, str(exc))

        report.extend(local_preflight().checks)
        if report.ok:
            start_monitor(True)
            monitor_started = _wait_for_monitor_ready(monitor_previous)
            report.add(
                "Windows-Monitor",
                monitor_started,
                "Ready-Heartbeat empfangen" if monitor_started else "kein Ready-Heartbeat",
                required=False,
            )

            sentinel = f"LOCAL-CODEX-SELF-TEST-{secrets.token_hex(8)}"
            command = forced_local_codex_command([
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--json",
                f"Antworte exakt mit: {sentinel}",
            ])
            try:
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=local_environment(),
                    text=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=180,
                )
                events = []
                invalid_lines = 0
                for line in completed.stdout.splitlines():
                    if not line.strip():
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        invalid_lines += 1
                response_ok = completed.returncode == 0 and any(
                    _contains_text(event, sentinel) for event in events
                )
                detail = f"Exit {completed.returncode}, {len(events)} JSON-Events"
                if invalid_lines:
                    detail += f", {invalid_lines} ungültige Zeilen"
                if not response_ok and completed.stderr:
                    detail += f", stderr={completed.stderr[-300:].strip()}"
                report.add("Echte Codex-Inferenz", response_ok, detail)
            except subprocess.TimeoutExpired:
                report.add("Echte Codex-Inferenz", False, "Timeout nach 180 Sekunden")

            try:
                snapshot = httpx.get(f"{ROUTER_URL}/monitor/snapshot", timeout=4.0).json()
                tokens = snapshot.get("tokens", {})
                turn = snapshot.get("turn", {})
                telemetry_ok = (
                    turn.get("model") == SETTINGS.build_model
                    and tokens.get("exact") is True
                    and int(tokens.get("input") or 0) > 0
                    and int(tokens.get("output") or 0) > 0
                )
                report.add(
                    "Ollama-Telemetrie",
                    telemetry_ok,
                    f"model={turn.get('model', '-')}, input={tokens.get('input', '-')}, output={tokens.get('output', '-')}"
                )
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                report.add("Ollama-Telemetrie", False, str(exc))

            try:
                ps = httpx.get(f"{SETTINGS.ollama_base_url}/api/ps", timeout=4.0).json()
                loaded = {
                    item.get("name") for item in ps.get("models", []) if isinstance(item, dict)
                }
                report.add(
                    "Geladenes Ollama-Modell",
                    SETTINGS.build_model in loaded,
                    ", ".join(sorted(str(item) for item in loaded)) or "kein Modell geladen",
                )
            except (httpx.HTTPError, ValueError) as exc:
                report.add("Geladenes Ollama-Modell", False, str(exc))
    finally:
        heartbeat_stop.set()
        if heartbeat is not None:
            heartbeat.join(timeout=2)
        release_lease(lease_id)
        if proxy is not None:
            deadline = time.monotonic() + 35
            while router_ready() and time.monotonic() < deadline:
                time.sleep(0.5)
            router_stopped = not router_ready()
            if not router_stopped:
                proxy.terminate()
                try:
                    proxy.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proxy.kill()
            report.add("Router-Shutdown", router_stopped, "beendet" if router_stopped else "musste beendet werden")
        else:
            report.add("Router-Shutdown", True, "geteilte Router-Instanz bleibt aktiv", required=False)

        if monitor_started and proxy is not None:
            deadline = time.monotonic() + 40
            while _ready_timestamp() is not None and time.monotonic() < deadline:
                time.sleep(0.5)
            report.add(
                "Monitor-Shutdown",
                _ready_timestamp() is None,
                "beendet" if _ready_timestamp() is None else "Ready-Datei blieb aktiv",
                required=False,
            )

    print_report(report, json_output)
    return 0 if report.ok else 1


def setup_context_arguments(arguments: list[str]) -> list[str]:
    """Return an optional --context override for the setup subcommand."""
    for index, argument in enumerate(arguments):
        if argument == "--context":
            return arguments[index:index + 2]
        if argument.startswith("--context="):
            return [argument]
    return []


def ensure_setup(benchmark: bool = False, context_arguments: list[str] | None = None) -> None:
    config_version = 0
    monitor_missing = True
    try:
        import json

        runtime = json.loads((LOCAL_HOME / "runtime.json").read_text())
        config_version = int(runtime["config_version"])
        monitor = runtime.get("monitor") if isinstance(runtime, dict) else None
        monitor_executable = monitor.get("wsl_executable") if isinstance(monitor, dict) else None
        monitor_missing = not isinstance(monitor_executable, str) or not Path(monitor_executable).exists()
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if context_arguments or benchmark or not (LOCAL_HOME / "config.toml").exists():
        command = [str(PYTHON), "-m", "local_codex.setup"]
        if benchmark:
            command.append("--benchmark")
        if context_arguments:
            command.extend(context_arguments)
        subprocess.run(command, cwd=ROOT, check=True)
    elif config_version < 9 or monitor_missing:
        subprocess.run(
            [str(PYTHON), "-m", "local_codex.setup", "--refresh-config"],
            cwd=ROOT,
            check=True,
        )


def main() -> int:
    arguments = map_cli_paths(sys.argv[1:])
    monitor_enabled = "--no-monitor" not in arguments
    arguments = [argument for argument in arguments if argument != "--no-monitor"]
    if arguments and arguments[0] in {"--setup", "setup"}:
        ensure_setup(
            benchmark="--benchmark" in arguments[1:],
            context_arguments=setup_context_arguments(arguments[1:]),
        )
        start_search()
        return 0
    if arguments and arguments[0] == "--search-doctor":
        ensure_setup()
        return search_doctor()
    if arguments and arguments[0] == "--monitor-doctor":
        return monitor_doctor()
    if arguments and arguments[0] in {"--doctor", "doctor"}:
        return local_doctor("--json" in arguments[1:])
    if arguments and arguments[0] in {"--self-test", "self-test"}:
        return local_self_test("--json" in arguments[1:])
    if arguments and arguments[0] in {"--usage", "usage", "--usage-json"}:
        ensure_setup()
        from local_codex.usage import UsageStore, format_summary

        store = UsageStore(LOCAL_HOME / "state" / "usage.sqlite3")
        summary = store.summary(None if "--all" in arguments[1:] else 30)
        store.close()
        if arguments[0] == "--usage-json":
            import json

            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(format_summary(summary))
        return 0
    option_boundary = arguments.index("--") if "--" in arguments else len(arguments)
    force = "--force" in arguments[:option_boundary]
    try:
        arguments = validate_local_arguments(arguments)
    except LocalArgumentError as exc:
        print(f"[local-codex] Abbruch: {exc}", file=sys.stderr)
        return 2

    ensure_setup()
    proxy = None
    router_error = None
    try:
        proxy = start_router()
    except RuntimeError as exc:
        router_error = str(exc)
    lease_id = register_lease()
    heartbeat_stop = threading.Event()
    heartbeat = None
    if lease_id:
        heartbeat = threading.Thread(
            target=lease_heartbeat, args=(lease_id, heartbeat_stop), daemon=True
        )
        heartbeat.start()
    preflight = DiagnosticReport()
    if router_error:
        preflight.add("Router-Start", False, router_error)
    preflight.extend(local_preflight().checks)
    if not preflight.ok:
        print_report(preflight)
        if not confirm_failed_preflight(force):
            heartbeat_stop.set()
            if heartbeat is not None:
                heartbeat.join(timeout=2)
            release_lease(lease_id)
            if proxy is not None:
                proxy.terminate()
                try:
                    proxy.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proxy.kill()
            return 1
    else:
        ollama = next((check.detail for check in preflight.checks if check.name == "Ollama"), "bereit")
        print(
            f"[local-codex] Verifiziert: model=local-codex provider=local_router, {ollama}, router={ROUTER_URL}"
        )

    start_search()
    start_monitor(monitor_enabled)
    environment = local_environment()
    exit_code = 0
    try:
        exit_code = subprocess.run(
            forced_local_codex_command(arguments), env=environment
        ).returncode
    except KeyboardInterrupt:
        exit_code = 130
    finally:
        heartbeat_stop.set()
        if heartbeat is not None:
            heartbeat.join(timeout=2)
        release_lease(lease_id)
        stop_monitor_when_idle()
        # Managed routers normally stop themselves after the last lease. If
        # registration failed, retain the old ownership cleanup as a fallback.
        if proxy is not None and lease_id is None:
            proxy.terminate()
            try:
                proxy.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proxy.kill()
    from local_codex.usage import UsageStore, format_summary

    store = UsageStore(LOCAL_HOME / "state" / "usage.sqlite3")
    summary = store.summary(1)
    store.close()
    if summary["calls"]:
        input_tokens = f"{summary['input_tokens']:,}".replace(",", ".")
        output_tokens = f"{summary['output_tokens']:,}".replace(",", ".")
        print(
            f"\n[local-codex] Letzte 24h: {input_tokens} Input / "
            f"{output_tokens} Output-Tokens; "
            f"API-Vergleich/Gespart: ${summary['estimated_saved_usd']:.6f}"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
