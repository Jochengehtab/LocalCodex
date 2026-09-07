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
from pathlib import Path

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
from local_codex.settings import LOCAL_HOME, SETTINGS


ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "bin" / "python"
HEALTH_URL = "http://127.0.0.1:18081/health"
ROUTER_URL = "http://127.0.0.1:18081"
SEARCH_URL = "http://127.0.0.1:18082"
SEARCH_COMPOSE = ROOT / "local_search" / "docker-compose.yml"
SEARCH_ENV = LOCAL_HOME / "search.env"
ROUTER_LOG = LOCAL_HOME / "state" / "router.log"


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
    executable = runtime.get("wsl_executable") or runtime.get("executable")
    if not isinstance(executable, str) or not Path(executable).exists():
        print("Hinweis: Nativer Monitor ist nicht installiert; `codex-local setup --build-monitor` repariert ihn.")
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
    executable = runtime.get("wsl_executable") or runtime.get("executable")
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Dear-ImGui-Monitor", isinstance(executable, str) and Path(executable).exists(), str(executable or "nicht installiert")))
    checks.append(("Router", router_ready(), HEALTH_URL))
    process = start_monitor(checks[0][1])
    time.sleep(1.0)
    launched = process is not None and process.poll() in (None, 0)
    checks.append(("Native UI", launched, "gestartet/Singleton aktiv" if launched else "Start fehlgeschlagen"))
    for name, ok, detail in checks:
        print(f"[{'OK' if ok else 'FEHLER'}] {name}: {detail}")
    release_lease(lease_id)
    if process is not None and process.poll() is None:
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.terminate()
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


def local_self_test(json_output: bool = False) -> int:
    ensure_setup()
    report = DiagnosticReport()
    proxy = None
    lease_id = None
    heartbeat_stop = threading.Event()
    heartbeat = None
    monitor_started = False
    monitor_process = None
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
            monitor_process = start_monitor(True)
            time.sleep(1.0)
            monitor_started = monitor_process is not None and monitor_process.poll() in (None, 0)
            report.add(
                "Windows-Monitor",
                monitor_started,
                "Native UI gestartet/Singleton aktiv" if monitor_started else "Monitorstart fehlgeschlagen",
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

        if monitor_started and proxy is not None and monitor_process is not None:
            try:
                monitor_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                monitor_process.terminate()
            report.add(
                "Monitor-Shutdown",
                monitor_process.poll() is not None,
                "beendet" if monitor_process.poll() is not None else "musste beendet werden",
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


def ensure_setup(
    benchmark: bool = False,
    context_arguments: list[str] | None = None,
    build_monitor: bool = False,
    refresh_config: bool = False,
) -> None:
    config_version = 0
    monitor_missing = True
    runtime = {}
    try:
        import json

        runtime = json.loads((LOCAL_HOME / "runtime.json").read_text())
        config_version = int(runtime["config_version"])
        monitor = runtime.get("monitor") if isinstance(runtime, dict) else None
        monitor_executable = monitor.get("wsl_executable") if isinstance(monitor, dict) else None
        monitor_missing = not isinstance(monitor_executable, str) or not Path(monitor_executable).exists()
    except (OSError, ValueError, KeyError, TypeError):
        runtime = {}
    from dataclasses import asdict
    from local_codex.config import load_model_config

    profiles_changed = False
    if (LOCAL_HOME / "localcodex.toml").is_file() or runtime.get("model_profiles") is not None:
        profiles_changed = runtime.get("model_profiles") != asdict(
            load_model_config(LOCAL_HOME / "localcodex.toml")
        )
    if profiles_changed or refresh_config or context_arguments or benchmark or build_monitor or not (LOCAL_HOME / "config.toml").exists():
        command = [str(PYTHON), "-m", "local_codex.setup"]
        if refresh_config and not profiles_changed:
            command.append("--refresh-config")
        if benchmark:
            command.append("--benchmark")
        if context_arguments:
            command.extend(context_arguments)
        if build_monitor:
            command.append("--build-monitor")
        subprocess.run(command, cwd=ROOT, check=True)
    elif config_version < 11 or monitor_missing:
        subprocess.run(
            [str(PYTHON), "-m", "local_codex.setup", "--refresh-config"],
            cwd=ROOT,
            check=True,
        )


def main() -> int:
    arguments = map_cli_paths(sys.argv[1:])
    monitor_enabled = "--no-monitor" not in arguments
    arguments = [argument for argument in arguments if argument != "--no-monitor"]
    from local_codex.commands import LauncherCommands, dispatch
    result = dispatch(arguments, LauncherCommands(
        root=ROOT, python=PYTHON, home=LOCAL_HOME, setup=ensure_setup,
        context_arguments=setup_context_arguments, start_search=start_search,
        search_doctor=search_doctor, monitor_doctor=monitor_doctor,
        doctor=local_doctor, self_test=local_self_test,
    ))
    if result is not None:
        return result
    option_boundary = arguments.index("--") if "--" in arguments else len(arguments)
    force = "--force" in arguments[:option_boundary]
    try:
        arguments = validate_local_arguments(arguments)
    except LocalArgumentError as exc:
        print(f"[local-codex] Abbruch: {exc}", file=sys.stderr)
        return 2

    from local_codex.releases import maybe_prompt_for_update

    if maybe_prompt_for_update():
        return 0
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
        # Managed routers normally stop themselves after the last lease. If
        # registration failed, retain the old ownership cleanup as a fallback.
        if proxy is not None and lease_id is None:
            proxy.terminate()
            try:
                proxy.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proxy.kill()
    from local_codex.usage import UsageStore

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
