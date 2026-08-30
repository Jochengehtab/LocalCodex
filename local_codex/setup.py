from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import secrets
import shutil
import statistics
import struct
import subprocess
import threading
import time
import zlib
from dataclasses import asdict
from pathlib import Path, PureWindowsPath
from typing import Any

import httpx

from .settings import LOCAL_HOME, LOCAL_INSTRUCTIONS, MODELS_DIR, ROOT, SETTINGS, STATE_DIR


RAW_MODELS = {
    "plan": "qwen3.8:27b",
    "build": "qwen3.6:35b-a3b",
    "vision": "qwen3-vl:30b",
}
FINAL_MODELS = {
    "plan": SETTINGS.plan_model,
    "build": SETTINGS.build_model,
    "vision": SETTINGS.vision_model,
}
# Qwen3.x advertises a native 256K window, but the usable value depends on
# available VRAM/RAM and KV-cache format.  The benchmark decides which of
# these profiles is safe instead of silently selecting an OOM-prone maximum.
CONTEXT_CANDIDATES = (8192, 16384, 32768, 65536, 131072)
GIB = 1024 * 1024 * 1024
CONFIG_VERSION = 9
MONITOR_PROJECT = ROOT / "windows" / "LocalCodexMonitor" / "LocalCodexMonitor.csproj"


def run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )


def available_models() -> set[str]:
    response = httpx.get(f"{SETTINGS.ollama_base_url}/api/tags", timeout=10.0)
    response.raise_for_status()
    return {item["name"] for item in response.json().get("models", [])}


def verify_prerequisites() -> None:
    run(["codex", "--version"])
    installed = available_models()
    missing = [model for model in RAW_MODELS.values() if model not in installed]
    if missing:
        raise RuntimeError(f"Fehlende Ollama-Modelle: {', '.join(missing)}")


def create_alias(alias: str, source: str, context: int) -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = alias.replace(":", "-")
    modelfile = MODELS_DIR / f"{safe_name}.Modelfile"
    modelfile.write_text(
        f"FROM {source}\nPARAMETER num_ctx {context}\nPARAMETER temperature 0.2\n",
        encoding="utf-8",
    )
    run(["ollama", "create", alias, "-f", str(modelfile)])
    return modelfile


def remove_alias(alias: str) -> None:
    subprocess.run(
        ["ollama", "rm", alias],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def memory_snapshot() -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, raw = line.split(":", 1)
        values[key] = int(raw.strip().split()[0]) * 1024
    swap_used = values.get("SwapTotal", 0) - values.get("SwapFree", 0)
    return values.get("MemAvailable", 0), swap_used


class MemorySampler:
    def __init__(self):
        self.minimum_available = 2**63 - 1
        self.maximum_swap = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.1):
            available, swap = memory_snapshot()
            self.minimum_available = min(self.minimum_available, available)
            self.maximum_swap = max(self.maximum_swap, swap)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join(timeout=2.0)


def unload_model(model: str) -> None:
    try:
        httpx.post(
            f"{SETTINGS.ollama_base_url}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=15.0,
        )
    except httpx.HTTPError:
        pass


def benchmark_image_url() -> str:
    """Build a tiny red/blue PNG without adding an image dependency."""
    width = height = 64

    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = binascii.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    rows = []
    for _ in range(height):
        pixels = b"".join(
            b"\xff\x00\x00" if x < width // 2 else b"\x00\x00\xff"
            for x in range(width)
        )
        rows.append(b"\x00" + pixels)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def benchmark_model(alias: str, role: str, repetitions: int = 2) -> dict[str, Any]:
    tool = {
        "type": "function",
        "name": "exec_command",
        "description": "Run a shell command",
        "parameters": {
            "type": "object",
            "properties": {"cmd": {"type": "string"}},
            "required": ["cmd"],
            "additionalProperties": False,
        },
    }
    durations: list[float] = []
    minimum_available = 2**63 - 1
    maximum_swap = 0
    successes = 0
    for _ in range(repetitions):
        unload_model(alias)
        start = time.monotonic()
        with MemorySampler() as sampler:
            if role == "vision":
                request_body = {
                    "model": alias,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "Name the two main colors. Reply only with color names.",
                                },
                                {
                                    "type": "input_image",
                                    "image_url": benchmark_image_url(),
                                    "detail": "high",
                                },
                            ],
                        }
                    ],
                    "stream": False,
                }
            else:
                request_body = {
                    "model": alias,
                    "input": "Call exec_command exactly once with cmd set to pwd.",
                    "tools": [tool],
                    "stream": False,
                }
            try:
                response = httpx.post(
                    f"{SETTINGS.ollama_base_url}/v1/responses",
                    json=request_body,
                    timeout=SETTINGS.request_timeout_seconds,
                )
            except httpx.HTTPError:
                # A long-context load can fail before Ollama returns an HTTP
                # response (timeout, disconnect, or an OOM kill).  Treat that
                # repetition as a failed probe so setup can retain the last
                # known-good profile instead of aborting halfway through.
                response = None
        durations.append(time.monotonic() - start)
        minimum_available = min(minimum_available, sampler.minimum_available)
        maximum_swap = max(maximum_swap, sampler.maximum_swap)
        if response is not None and response.status_code == 200:
            output = response.json().get("output", [])
            if role == "vision":
                answer = " ".join(
                    part.get("text", "")
                    for item in output
                    if item.get("type") == "message"
                    for part in item.get("content", [])
                    if isinstance(part, dict)
                ).lower()
                passed = "red" in answer and "blue" in answer
            else:
                passed = any(item.get("type") == "function_call" for item in output)
            if passed:
                successes += 1
    return {
        "successes": successes,
        "repetitions": repetitions,
        "median_seconds": statistics.median(durations),
        "minimum_available_bytes": minimum_available,
        "maximum_swap_bytes": maximum_swap,
    }


def benchmark_contexts() -> tuple[int, dict[str, Any]]:
    baseline_swap = memory_snapshot()[1]
    report: dict[str, Any] = {}
    aliases: list[str] = []
    try:
        for context in CONTEXT_CANDIDATES:
            context_report: dict[str, Any] = {}
            for role, source in RAW_MODELS.items():
                alias = f"local-codex-{role}-{context}"
                aliases.append(alias)
                create_alias(alias, source, context)
                result = benchmark_model(alias, role)
                context_report[role] = result
                measurement = "Bildantworten" if role == "vision" else "Tool-Calls"
                print(
                    f"{context // 1024}K {role}: {result['successes']}/"
                    f"{result['repetitions']} {measurement}, Median "
                    f"{result['median_seconds']:.1f}s"
                )
            report[str(context)] = context_report

            # Context memory is monotonic for a fixed model/quantization.  If
            # a profile already fails its correctness/resource checks, do not
            # probe an even larger profile: that would only increase the risk
            # of swapping or an OOM kill.  The selection pass below will keep
            # the largest profile that passed before this point.
            if any(
                result["successes"] < result["repetitions"]
                or result["minimum_available_bytes"] < 3 * GIB
                or result["maximum_swap_bytes"] - baseline_swap > GIB
                for result in context_report.values()
            ):
                break

        baseline = report[str(CONTEXT_CANDIDATES[0])]
        passing: list[int] = []
        for context in CONTEXT_CANDIDATES:
            current = report.get(str(context))
            if current is None:
                # A failed profile stops the ascending probe; larger profiles
                # were intentionally not attempted.
                break
            stable = all(
                result["successes"] == result["repetitions"]
                and result["minimum_available_bytes"] >= 3 * GIB
                and result["maximum_swap_bytes"] - baseline_swap <= GIB
                and (
                    context == CONTEXT_CANDIDATES[0]
                    or result["median_seconds"] <= 2 * baseline[role]["median_seconds"]
                )
                for role, result in current.items()
            )
            if stable:
                passing.append(context)
        if passing:
            return max(passing), report
        raise RuntimeError("Keines der Kontextprofile hat den Stabilitätstest bestanden")
    finally:
        for alias in aliases:
            remove_alias(alias)


def generate_model_catalog(context: int) -> None:
    raw = run(["codex", "debug", "models", "--bundled"]).stdout
    catalog = json.loads(raw)
    template = next(
        model for model in catalog["models"] if model.get("slug") == "gpt-5.4"
    )
    template["slug"] = SETTINGS.public_model
    template["display_name"] = "Local Qwen Codex"
    template["description"] = "Local Ollama router for Qwen coding and vision models"
    template["visibility"] = "list"
    template["upgrade"] = None
    template["priority"] = 1
    template["default_reasoning_level"] = "xhigh"
    # Request summaries rather than raw thought so the optional local monitor
    # can expose useful progress without expanding Codex context.
    template["default_reasoning_summary"] = "auto"
    supported_reasoning = template.get("supported_reasoning_levels")
    if isinstance(supported_reasoning, list) and not any(
        isinstance(item, dict) and item.get("effort") == "xhigh"
        for item in supported_reasoning
    ):
        supported_reasoning.append({"effort": "xhigh", "description": "Maximum local reasoning depth"})
    template["context_window"] = context
    template["max_context_window"] = context
    template["effective_context_window_percent"] = 80
    template["supports_search_tool"] = False
    template.pop("web_search_tool_type", None)
    template["support_verbosity"] = False
    template["use_responses_lite"] = False
    template["tool_mode"] = None
    template["apply_patch_tool_type"] = None
    template["base_instructions"] = LOCAL_INSTRUCTIONS
    template["include_skills_usage_instructions"] = False
    template["include_plugin_usage_instructions"] = False
    template["include_apps_usage_instructions"] = False
    if isinstance(template.get("model_messages"), dict):
        template["model_messages"]["instructions_template"] = LOCAL_INSTRUCTIONS
    (LOCAL_HOME / "model_catalog.json").write_text(
        json.dumps({"models": [template]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_codex_config(context: int) -> None:
    compact_limit = int(context * 0.7)
    catalog_path = LOCAL_HOME / "model_catalog.json"
    instructions_path = LOCAL_HOME / "instructions.md"
    config = f'''model = "{SETTINGS.public_model}"
model_provider = "local_router"
model_catalog_json = "{catalog_path}"
model_instructions_file = "{instructions_path}"
model_context_window = {context}
model_auto_compact_token_limit = {compact_limit}
model_reasoning_effort = "xhigh"
approval_policy = "on-request"
sandbox_mode = "workspace-write"
web_search = "disabled"
check_for_update_on_startup = false
suppress_unstable_features_warning = true

[sandbox_workspace_write]
network_access = false

[model_providers.local_router]
name = "Local Qwen Router"
base_url = "http://{SETTINGS.host}:{SETTINGS.port}/v1"
wire_api = "responses"
request_max_retries = 1
stream_max_retries = 0
stream_idle_timeout_ms = 900000

[analytics]
enabled = false

[feedback]
enabled = false

[mcp_servers.local_search]
command = "{ROOT / '.venv/bin/python'}"
args = ["-m", "local_search.mcp_server"]
cwd = "{ROOT}"
startup_timeout_sec = 20
tool_timeout_sec = 30
enabled_tools = ["web_search", "fetch_page"]

[mcp_servers.local_search.env]
SEARXNG_URL = "http://127.0.0.1:18082"
LOCAL_SEARCH_CACHE = "{STATE_DIR / 'web_cache.sqlite3'}"

[[skills.config]]
path = "{LOCAL_HOME}/skills/.system/imagegen/SKILL.md"
enabled = false

[[skills.config]]
path = "{LOCAL_HOME}/skills/.system/openai-docs/SKILL.md"
enabled = false

[[skills.config]]
path = "{LOCAL_HOME}/skills/.system/plugin-creator/SKILL.md"
enabled = false

[[skills.config]]
path = "{LOCAL_HOME}/skills/.system/review-agent/SKILL.md"
enabled = false

[[skills.config]]
path = "{LOCAL_HOME}/skills/.system/skill-creator/SKILL.md"
enabled = false

[[skills.config]]
path = "{LOCAL_HOME}/skills/.system/skill-installer/SKILL.md"
enabled = false

[features]
apps = false
browser_use = false
browser_use_external = false
browser_use_full_cdp_access = false
computer_use = false
enable_request_compression = false
fast_mode = false
image_generation = false
in_app_browser = false
in_app_updates = false
multi_agent = false
plugins = false
recommended_plugins = false
remote_plugin = false
skill_mcp_dependency_install = false
skill_search = false
skip_host_skill_discovery = true

[projects."{Path.home()}"]
trust_level = "trusted"

[projects."{ROOT}"]
trust_level = "trusted"
'''
    (LOCAL_HOME / "config.toml").write_text(config, encoding="utf-8")
    instructions_path.write_text(LOCAL_INSTRUCTIONS, encoding="utf-8")


def validate_config() -> None:
    result = subprocess.run(
        ["codex", "--strict-config", "doctor", "--json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "CODEX_HOME": str(LOCAL_HOME)},
    )
    try:
        report = json.loads(result.stdout)
        config_check = report["checks"]["config.load"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise RuntimeError(
            f"Codex-Konfiguration konnte nicht geprüft werden:\n{result.stdout}"
        ) from None
    if config_check.get("status") != "ok":
        raise RuntimeError(f"Codex-Konfiguration ungültig:\n{result.stdout}")


def ensure_search_secret() -> None:
    search_env = LOCAL_HOME / "search.env"
    if not search_env.exists():
        search_env.write_text(f"SEARXNG_SECRET={secrets.token_hex(32)}\n", encoding="utf-8")
        search_env.chmod(0o600)


def install_windows_monitor() -> dict[str, Any]:
    """Publish the native monitor to Windows LocalAppData without blocking Codex on failure."""
    result: dict[str, Any] = {"installed": False}
    powershell = shutil.which("powershell.exe")
    dotnet = shutil.which("dotnet.exe")
    wslpath = shutil.which("wslpath")
    if not powershell or not dotnet or not wslpath or not MONITOR_PROJECT.exists():
        result["error"] = "powershell.exe, dotnet.exe, wslpath oder Monitorprojekt fehlt"
        return result
    try:
        local_app_data = run([
            powershell, "-NoProfile", "-Command",
            "[Environment]::GetFolderPath('LocalApplicationData')",
        ]).stdout.strip()
        # Publish into a versioned directory.  The monitor is intentionally a
        # tray singleton and may still be running while setup refreshes the
        # router; replacing its loaded DLL in-place would fail on Windows.
        # The next Codex start reads this runtime entry and launches the new
        # side-by-side build without interrupting the current turn.
        install_windows = str(PureWindowsPath(local_app_data) / "LocalCodexMonitor" / f"app-v{CONFIG_VERSION}")
        project_windows = run([wslpath, "-w", str(MONITOR_PROJECT)]).stdout.strip()
        run([
            dotnet, "publish", project_windows, "-c", "Release", "-r", "win-x64",
            "--self-contained", "false", "--nologo", "-o", install_windows,
            "-p:DebugType=None", "-p:DebugSymbols=false",
        ])
        executable_windows = str(PureWindowsPath(install_windows) / "LocalCodexMonitor.exe")
        executable_wsl = run([wslpath, "-u", executable_windows]).stdout.strip()
        ready_windows = str(PureWindowsPath(local_app_data) / "LocalCodexMonitor" / "monitor.ready.json")
        ready_wsl = run([wslpath, "-u", ready_windows]).stdout.strip()
        log_windows = str(PureWindowsPath(local_app_data) / "LocalCodexMonitor" / "logs" / "monitor.log")
        log_wsl = run([wslpath, "-u", log_windows]).stdout.strip()
        result.update({
            "installed": Path(executable_wsl).exists(),
            "windows_executable": executable_windows,
            "wsl_executable": executable_wsl,
            "windows_ready_file": ready_windows,
            "wsl_ready_file": ready_wsl,
            "windows_log_file": log_windows,
            "wsl_log_file": log_wsl,
            "target_framework": "net10.0-windows",
        })
        if not result["installed"]:
            result["error"] = "Veröffentlichte Monitor-EXE wurde nicht gefunden"
    except (OSError, subprocess.CalledProcessError) as exc:
        result["error"] = str(exc)
    return result


def refresh_config() -> dict[str, Any]:
    LOCAL_HOME.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    runtime_path = LOCAL_HOME / "runtime.json"
    runtime: dict[str, Any] = {}
    if runtime_path.exists():
        try:
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    context = int(runtime.get("context_window", 8192))
    generate_model_catalog(context)
    write_codex_config(context)
    runtime["context_window"] = context
    runtime["models"] = FINAL_MODELS
    runtime["settings"] = asdict(SETTINGS)
    runtime["monitor"] = install_windows_monitor()
    runtime["config_version"] = CONFIG_VERSION
    runtime_path.write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ensure_search_secret()
    validate_config()
    return runtime


def install(context: int | None, benchmark: bool) -> dict[str, Any]:
    verify_prerequisites()
    LOCAL_HOME.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    previous_runtime: dict[str, Any] = {}
    runtime_path = LOCAL_HOME / "runtime.json"
    if runtime_path.exists():
        try:
            previous_runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    report: dict[str, Any] = previous_runtime.get("hardware_benchmark", {})
    if benchmark:
        selected_context, report = benchmark_contexts()
    else:
        selected_context = context or previous_runtime.get("context_window", 8192)
    for role, source in RAW_MODELS.items():
        create_alias(FINAL_MODELS[role], source, selected_context)
    generate_model_catalog(selected_context)
    write_codex_config(selected_context)
    runtime = {
        "config_version": CONFIG_VERSION,
        "context_window": selected_context,
        "models": FINAL_MODELS,
        "hardware_benchmark": report,
        "settings": asdict(SETTINGS),
        "monitor": install_windows_monitor(),
    }
    runtime_path.write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    validate_config()
    ensure_search_secret()
    return runtime


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up local Codex for Ollama")
    parser.add_argument("--benchmark", action="store_true", help="Test 8K, 16K, 32K, 64K and 128K")
    parser.add_argument("--context", type=int, choices=CONTEXT_CANDIDATES)
    parser.add_argument("--refresh-config", action="store_true", help="Refresh Codex and MCP config without recreating Ollama models")
    args = parser.parse_args()
    if args.refresh_config:
        runtime = refresh_config()
        print(f"Lokale Codex-Konfiguration aktualisiert: {runtime['context_window']} Tokens Kontext")
        return
    runtime = install(args.context, args.benchmark)
    print(f"Lokaler Codex eingerichtet: {runtime['context_window']} Tokens Kontext")


if __name__ == "__main__":
    main()
