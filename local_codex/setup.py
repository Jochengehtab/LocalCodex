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
import sys
import threading
import time
import zlib
from dataclasses import asdict
from pathlib import Path, PureWindowsPath
from typing import Any

import httpx

from .i18n import tr
from .settings import (
    LOCAL_HOME,
    LOCAL_INSTRUCTIONS,
    MODELS_DIR,
    ROOT,
    SETTINGS,
    SOURCE_MODELS,
    MODEL_CONFIG,
    STATE_DIR,
)


RAW_MODELS = SOURCE_MODELS
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
CONFIG_VERSION = 11
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


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
        raise RuntimeError(tr("setup.missing_models", models=", ".join(missing)))
    from .model_validation import validate_model_info
    for role, profile in MODEL_CONFIG.profiles().items():
        response = httpx.post(f"{SETTINGS.ollama_base_url}/api/show", json={"model": profile.source}, timeout=10.0, trust_env=False)
        response.raise_for_status()
        validate_model_info(profile.source, role, response.json(), profile.reasoning)


def verify_benchmark_idle() -> None:
    """Refuse to benchmark while the managed router is serving a Codex session."""
    try:
        response = httpx.get(f"http://{SETTINGS.host}:{SETTINGS.port}/monitor/snapshot", timeout=1.0)
        if response.status_code != 200:
            return
        snapshot = response.json()
        sessions = snapshot.get("sessions") if isinstance(snapshot.get("sessions"), dict) else {}
        if snapshot.get("active") or int(sessions.get("active_count", 0)) > 0:
            raise RuntimeError(tr("setup.benchmark_active"))
    except httpx.HTTPError:
        return


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
    throughputs: list[float] = []
    minimum_available = 2**63 - 1
    maximum_swap = 0
    maximum_model_bytes = 0
    maximum_vram_bytes = 0
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
        try:
            loaded = httpx.get(f"{SETTINGS.ollama_base_url}/api/ps", timeout=3.0).json().get("models", [])
            current = next((item for item in loaded if item.get("name") == alias), {})
            maximum_model_bytes = max(maximum_model_bytes, int(current.get("size") or 0))
            maximum_vram_bytes = max(maximum_vram_bytes, int(current.get("size_vram") or 0))
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        if response is not None and response.status_code == 200:
            payload = response.json()
            output = payload.get("output", [])
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            eval_count = payload.get("eval_count", usage.get("output_tokens"))
            eval_duration = payload.get("eval_duration")
            if isinstance(eval_count, (int, float)) and isinstance(eval_duration, (int, float)) and eval_duration > 0:
                throughputs.append(float(eval_count) / (float(eval_duration) / 1_000_000_000))
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
        "median_tokens_per_second": round(statistics.median(throughputs), 3) if throughputs else None,
        "minimum_available_bytes": minimum_available,
        "maximum_swap_bytes": maximum_swap,
        "model_size_bytes": maximum_model_bytes,
        "vram_size_bytes": maximum_vram_bytes,
    }


def benchmark_contexts(
    *, roles: tuple[str, ...] = ("plan", "build", "vision"),
    contexts: tuple[int, ...] = CONTEXT_CANDIDATES,
    repetitions: int = 2,
) -> tuple[dict[str, int], dict[str, Any]]:
    baseline_swap = memory_snapshot()[1]
    report: dict[str, Any] = {
        "schema_version": 2,
        "created_at": time.time(),
        "contexts": list(contexts),
        "repetitions": repetitions,
        "models": {},
    }
    recommendations: dict[str, int] = {}
    aliases: list[str] = []
    try:
        for alias in FINAL_MODELS.values():
            unload_model(alias)
        for role in roles:
            source = RAW_MODELS[role]
            role_report: dict[str, Any] = {"source_model": source, "profiles": {}}
            baseline: dict[str, Any] | None = None
            passing: list[int] = []
            for context in contexts:
                alias = f"local-codex-{role}-{context}"
                aliases.append(alias)
                create_alias(alias, source, context)
                result = benchmark_model(alias, role, repetitions=repetitions)
                role_report["profiles"][str(context)] = result
                measurement = tr("setup.vision_answers" if role == "vision" else "setup.tool_calls")
                print(tr(
                    "setup.benchmark_result", context=context // 1024, role=role,
                    successes=result["successes"], repetitions=result["repetitions"],
                    measurement=measurement, seconds=result["median_seconds"],
                ))
                baseline = baseline or result
                failed = (
                    result["successes"] < result["repetitions"]
                    or result["minimum_available_bytes"] < 3 * GIB
                    or result["maximum_swap_bytes"] - baseline_swap > GIB
                    or (
                        context != contexts[0]
                        and result["median_seconds"] > 2 * baseline["median_seconds"]
                    )
                )
                if failed:
                    break
                passing.append(context)
            if not passing:
                raise RuntimeError(f"No stable context profile for {role}")
            recommendations[role] = max(passing)
            role_report["recommended_context"] = recommendations[role]
            report["models"][role] = role_report
        report["recommendations"] = recommendations
        return recommendations, report
    finally:
        for alias in aliases:
            unload_model(alias)
            remove_alias(alias)


def generate_model_catalog(context: int) -> None:
    raw = run(["codex", "debug", "models", "--bundled"]).stdout
    catalog = json.loads(raw)
    candidates = [model for model in catalog.get("models", []) if isinstance(model, dict)]
    if not candidates:
        raise RuntimeError("Codex returned no model catalog; check the supported version matrix")
    template = next((model for model in candidates if model.get("slug") == "gpt-5.4"), candidates[0]).copy()
    template["slug"] = SETTINGS.public_model
    template["display_name"] = "Local Qwen Codex"
    template["description"] = "Local Ollama router for Qwen coding and vision models"
    template["visibility"] = "list"
    template["upgrade"] = None
    template["priority"] = 1
    template["default_reasoning_level"] = "xhigh"
    template.pop("default_reasoning_summary", None)
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
    template["include_skills_usage_instructions"] = True
    template["include_plugin_usage_instructions"] = True
    template["include_apps_usage_instructions"] = True
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


def _existing_monitor() -> dict[str, Any] | None:
    try:
        runtime = json.loads((LOCAL_HOME / "runtime.json").read_text(encoding="utf-8"))
        monitor = runtime.get("monitor")
        executable = monitor.get("wsl_executable") or monitor.get("executable")
        if isinstance(monitor, dict) and isinstance(executable, str) and Path(executable).exists():
            return monitor
    except (OSError, ValueError, TypeError):
        pass
    return None


def install_native_monitor(build_monitor: bool = False) -> dict[str, Any]:
    """Install a prebuilt or explicitly developer-built Dear ImGui monitor."""
    result: dict[str, Any] = {"installed": False, "framework": "dear-imgui-sdl3"}
    is_wsl = bool(os.environ.get("WSL_DISTRO_NAME"))
    existing = _existing_monitor()
    prebuilt_override = os.environ.get("LOCAL_CODEX_MONITOR_EXE")
    if not build_monitor and existing and not prebuilt_override:
        return existing

    if not is_wsl:
        candidate = Path(os.environ.get(
            "LOCAL_CODEX_MONITOR_EXE",
            ROOT / "build" / "native" / "monitor" / "localcodex-monitor",
        ))
        if build_monitor:
            run(["cmake", "-S", str(ROOT), "-B", str(ROOT / "build" / "native"),
                 "-DCMAKE_BUILD_TYPE=Release", "-DLOCALCODEX_BUILD_TESTS=OFF"])
            run(["cmake", "--build", str(ROOT / "build" / "native"), "--config", "Release"])
        if not candidate.exists():
            result["error"] = "Monitor-Binary fehlt; setup --build-monitor baut sie lokal"
            return result
        target = LOCAL_HOME / "monitor" / f"v{VERSION}" / "localcodex-monitor"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, target)
        target.chmod(0o755)
        result.update({"installed": True, "executable": str(target), "platform": "linux-x64"})
        return result

    powershell = shutil.which("powershell.exe")
    cmake = shutil.which("cmake.exe")
    wslpath = shutil.which("wslpath")
    if not powershell or not wslpath:
        result["error"] = "powershell.exe oder wslpath fehlt"
        return result
    try:
        local_app_data = run([
            powershell, "-NoProfile", "-Command",
            "[Environment]::GetFolderPath('LocalApplicationData')",
        ]).stdout.strip()
        build_dir = ROOT / "build" / "windows-monitor"
        candidate = build_dir / "monitor" / "Release" / "localcodex-monitor.exe"
        if build_monitor:
            if not cmake:
                raise RuntimeError("cmake.exe fehlt")
            source_windows = run([wslpath, "-w", str(ROOT)]).stdout.strip()
            build_windows = run([wslpath, "-w", str(build_dir)]).stdout.strip()
            run([cmake, "-S", source_windows, "-B", build_windows,
                 "-A", "x64", "-DLOCALCODEX_BUILD_TESTS=OFF"])
            run([cmake, "--build", build_windows, "--config", "Release", "--parallel"])
        prebuilt = prebuilt_override
        if prebuilt:
            candidate = Path(prebuilt)
        if not candidate.exists():
            result["error"] = "Windows-Monitor fehlt; setup --build-monitor baut ihn lokal"
            return result
        install_windows = str(PureWindowsPath(local_app_data) / "LocalCodex" / "versions" / f"v{VERSION}")
        install_wsl = Path(run([wslpath, "-u", install_windows]).stdout.strip())
        install_wsl.mkdir(parents=True, exist_ok=True)
        executable_windows = str(PureWindowsPath(install_windows) / "localcodex-monitor.exe")
        executable_wsl = run([wslpath, "-u", executable_windows]).stdout.strip()
        if candidate.resolve() != Path(executable_wsl).resolve():
            shutil.copy2(candidate, executable_wsl)
        result.update({
            "installed": Path(executable_wsl).exists(),
            "windows_executable": executable_windows,
            "wsl_executable": executable_wsl,
            "platform": "windows-x64",
            "version": VERSION,
        })
        if not result["installed"]:
            result["error"] = "Veröffentlichte Monitor-EXE wurde nicht gefunden"
    except (OSError, subprocess.CalledProcessError) as exc:
        result["error"] = str(exc)
    return result


def refresh_config(build_monitor: bool = False) -> dict[str, Any]:
    LOCAL_HOME.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    runtime_path = LOCAL_HOME / "runtime.json"
    runtime: dict[str, Any] = {}
    if runtime_path.exists():
        try:
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    context_windows = runtime.get("context_windows")
    if not isinstance(context_windows, dict):
        context_windows = {role: int(runtime.get("context_window", 8192)) for role in RAW_MODELS}
    context = min(int(context_windows.get(role, 8192)) for role in RAW_MODELS)
    generate_model_catalog(context)
    write_codex_config(context)
    runtime["context_window"] = context
    runtime["context_windows"] = context_windows
    runtime["models"] = FINAL_MODELS
    runtime["settings"] = asdict(SETTINGS)
    runtime["monitor"] = install_native_monitor(build_monitor)
    runtime["config_version"] = CONFIG_VERSION
    runtime_path.write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ensure_search_secret()
    validate_config()
    return runtime


def install(context: int | None, benchmark: bool, build_monitor: bool = False) -> dict[str, Any]:
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
    previous_windows = previous_runtime.get("context_windows")
    if not isinstance(previous_windows, dict):
        previous_windows = {}
    report: dict[str, Any] = previous_runtime.get("hardware_benchmark", {})
    if benchmark:
        verify_benchmark_idle()
        recommendations, report = benchmark_contexts()
        apply_recommendations = (
            sys.stdin.isatty()
            and input(tr("setup.apply_contexts")).strip().lower() in {"y", "yes", "j", "ja"}
        )
        if apply_recommendations:
            selected_contexts = recommendations
        else:
            selected_contexts = {
                role: int(previous_windows.get(
                    role, previous_runtime.get("context_window", 8192)
                )) for role in RAW_MODELS
            }
    else:
        selected_contexts = {
            role: int(context or (profile.context if (LOCAL_HOME / "localcodex.toml").exists()
                                 else previous_windows.get(role, previous_runtime.get("context_window", profile.context))))
            for role, profile in MODEL_CONFIG.profiles().items()
        }
    for role, source in RAW_MODELS.items():
        create_alias(FINAL_MODELS[role], source, selected_contexts[role])
    advertised_context = min(selected_contexts.values())
    generate_model_catalog(advertised_context)
    write_codex_config(advertised_context)
    runtime = {
        "config_version": CONFIG_VERSION,
        "model_profiles": asdict(MODEL_CONFIG),
        "context_window": advertised_context,
        "context_windows": selected_contexts,
        "models": FINAL_MODELS,
        "hardware_benchmark": report,
        "settings": asdict(SETTINGS),
        "monitor": install_native_monitor(build_monitor),
    }
    runtime_path.write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    validate_config()
    ensure_search_secret()
    return runtime


def run_benchmark_command(
    *, roles: tuple[str, ...], contexts: tuple[int, ...], repetitions: int,
    apply: bool, assume_yes: bool, json_output: bool,
) -> dict[str, Any]:
    verify_prerequisites()
    verify_benchmark_idle()
    LOCAL_HOME.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    runtime_path = LOCAL_HOME / "runtime.json"
    try:
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        runtime = {}
    recommendations, report = benchmark_contexts(
        roles=roles, contexts=contexts, repetitions=repetitions
    )
    report_path = STATE_DIR / "context-benchmark.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    runtime["hardware_benchmark"] = report
    runtime_path.write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("\n" + tr("setup.recommendations"))
        for role, value in recommendations.items():
            print(f"  {role}: {value // 1024}K")
        print(tr("setup.report", path=report_path))

    should_apply = apply or assume_yes
    if not should_apply and sys.stdin.isatty():
        should_apply = input(tr("setup.apply_contexts")).strip().lower() in {"y", "yes", "j", "ja"}
    if not should_apply:
        return report

    previous = runtime.get("context_windows")
    if not isinstance(previous, dict):
        previous = {role: int(runtime.get("context_window", 8192)) for role in RAW_MODELS}
    selected = {role: int(previous.get(role, 8192)) for role in RAW_MODELS}
    selected.update(recommendations)
    try:
        for role, source in RAW_MODELS.items():
            create_alias(FINAL_MODELS[role], source, selected[role])
        advertised = min(selected.values())
        generate_model_catalog(advertised)
        write_codex_config(advertised)
    except Exception:
        for role, source in RAW_MODELS.items():
            try:
                create_alias(FINAL_MODELS[role], source, int(previous.get(role, 8192)))
            except (OSError, subprocess.CalledProcessError, httpx.HTTPError):
                pass
        raise
    runtime.update({
        "config_version": CONFIG_VERSION,
        "context_window": advertised,
        "context_windows": selected,
        "models": FINAL_MODELS,
        "hardware_benchmark": report,
        "settings": asdict(SETTINGS),
    })
    runtime_path.write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validate_config()
    print(tr("setup.applied"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up local Codex for Ollama")
    parser.add_argument("--benchmark", action="store_true", help="Test 8K, 16K, 32K, 64K and 128K")
    parser.add_argument("--context", type=int, choices=CONTEXT_CANDIDATES)
    parser.add_argument("--refresh-config", action="store_true", help="Refresh Codex and MCP config without recreating Ollama models")
    parser.add_argument("--build-monitor", action="store_true", help="Build the Dear ImGui monitor locally")
    parser.add_argument("--models", default="plan,build,vision", help="Comma-separated benchmark roles")
    parser.add_argument("--contexts", default=",".join(map(str, CONTEXT_CANDIDATES)), help="Comma-separated token windows")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--apply", action="store_true", help="Apply benchmark recommendations")
    parser.add_argument("--yes", action="store_true", help="Apply without prompting")
    parser.add_argument("--json", action="store_true", help="Print benchmark JSON")
    args = parser.parse_args()
    if args.refresh_config:
        runtime = refresh_config(args.build_monitor)
        print(tr("setup.config_updated", context=runtime["context_window"]))
        return
    if args.benchmark:
        roles = tuple(item.strip() for item in args.models.split(",") if item.strip())
        if not roles or any(role not in RAW_MODELS for role in roles):
            parser.error("--models must contain plan, build and/or vision")
        contexts = tuple(sorted({int(item.strip()) for item in args.contexts.split(",") if item.strip()}))
        if not contexts or any(value not in CONTEXT_CANDIDATES for value in contexts):
            parser.error("--contexts contains an unsupported context window")
        if not 1 <= args.repetitions <= 10:
            parser.error("--repetitions must be between 1 and 10")
        run_benchmark_command(
            roles=roles, contexts=contexts, repetitions=args.repetitions,
            apply=args.apply, assume_yes=args.yes, json_output=args.json,
        )
        return
    runtime = install(args.context, False, args.build_monitor)
    print(tr("setup.installed", context=runtime["context_window"]))


if __name__ == "__main__":
    main()
