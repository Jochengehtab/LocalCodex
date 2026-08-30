from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx


@dataclass(frozen=True)
class DiagnosticCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True


@dataclass
class DiagnosticReport:
    checks: list[DiagnosticCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks if check.required)

    def add(self, name: str, ok: bool, detail: str, *, required: bool = True) -> None:
        self.checks.append(DiagnosticCheck(name, ok, detail, required))

    def extend(self, checks: Iterable[DiagnosticCheck]) -> None:
        self.checks.extend(checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [asdict(check) for check in self.checks],
        }


class LocalArgumentError(ValueError):
    pass


def validate_local_arguments(arguments: list[str]) -> list[str]:
    """Reject CLI switches that could bypass the isolated local provider."""
    result: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            result.extend(arguments[index:])
            break
        if argument == "--force":
            index += 1
            continue
        if argument in {"--oss", "--ignore-user-config"}:
            raise LocalArgumentError(f"{argument} ist mit codex-local nicht erlaubt")
        if argument in {"-p", "--profile", "--local-provider"}:
            raise LocalArgumentError(f"{argument} kann den lokalen Router umgehen")
        if argument.startswith("--profile=") or argument.startswith("--local-provider="):
            raise LocalArgumentError(f"{argument.split('=', 1)[0]} kann den lokalen Router umgehen")
        if argument in {"-m", "--model"}:
            if index + 1 >= len(arguments):
                raise LocalArgumentError(f"Wert für {argument} fehlt")
            if arguments[index + 1] != "local-codex":
                raise LocalArgumentError("codex-local erlaubt ausschließlich --model local-codex")
            index += 2
            continue
        if argument.startswith("--model="):
            if argument.split("=", 1)[1] != "local-codex":
                raise LocalArgumentError("codex-local erlaubt ausschließlich --model local-codex")
            index += 1
            continue
        if argument in {"-c", "--config"}:
            if index + 1 >= len(arguments):
                raise LocalArgumentError(f"Wert für {argument} fehlt")
            _validate_config_override(arguments[index + 1])
            result.extend((argument, arguments[index + 1]))
            index += 2
            continue
        if argument.startswith("--config="):
            _validate_config_override(argument.split("=", 1)[1])
        result.append(argument)
        index += 1
    return result


def _validate_config_override(value: str) -> None:
    key = value.split("=", 1)[0].strip()
    if key == "model" or key == "model_provider" or key.startswith("model_providers."):
        raise LocalArgumentError(f"Konfigurations-Override {key} ist mit codex-local nicht erlaubt")


def forced_local_codex_command(arguments: list[str]) -> list[str]:
    return [
        "codex",
        "--strict-config",
        "-m",
        "local-codex",
        "-c",
        'model_provider="local_router"',
        "-c",
        'model_reasoning_effort="xhigh"',
        "-c",
        'plan_mode_reasoning_effort="xhigh"',
        *arguments,
    ]


def _doctor_payload(local_home: Path, environment: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(
        [
            "codex",
            "--strict-config",
            "-m",
            "local-codex",
            "-c",
            'model_provider="local_router"',
            "-c",
            'model_reasoning_effort="xhigh"',
            "-c",
            'plan_mode_reasoning_effort="xhigh"',
            "doctor",
            "--json",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=15,
        env=environment,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Codex Doctor lieferte kein JSON: {result.stdout[-500:]}") from exc


def inspect_codex_doctor(payload: dict[str, Any], local_home: Path) -> list[DiagnosticCheck]:
    checks = payload.get("checks") if isinstance(payload, dict) else None
    checks = checks if isinstance(checks, dict) else {}
    config = checks.get("config.load") if isinstance(checks.get("config.load"), dict) else {}
    config_details = config.get("details") if isinstance(config.get("details"), dict) else {}
    expected_home = str(local_home.resolve())
    actual_home = str(config_details.get("CODEX_HOME", ""))
    config_ok = (
        config.get("status") == "ok"
        and actual_home == expected_home
        and config_details.get("model") == "local-codex"
        and config_details.get("model provider") == "local_router"
    )
    auth = checks.get("auth.credentials") if isinstance(checks.get("auth.credentials"), dict) else {}
    auth_details = auth.get("details") if isinstance(auth.get("details"), dict) else {}
    auth_ok = auth.get("status") == "ok" and str(
        auth_details.get("model provider requires OpenAI auth", "")
    ).lower() == "false"
    reachability = checks.get("network.provider_reachability")
    reachability = reachability if isinstance(reachability, dict) else {}
    return [
        DiagnosticCheck(
            "Codex-Konfiguration",
            config_ok,
            (
                f"CODEX_HOME={actual_home or '-'}, model={config_details.get('model', '-')}, "
                f"provider={config_details.get('model provider', '-')}"
            ),
        ),
        DiagnosticCheck(
            "Cloud-Sperre",
            auth_ok,
            "OpenAI-Authentifizierung ist nicht erforderlich" if auth_ok else str(auth.get("summary", "nicht bestätigt")),
        ),
        DiagnosticCheck(
            "Provider-Erreichbarkeit",
            reachability.get("status") == "ok",
            str(reachability.get("summary", "nicht geprüft")),
        ),
    ]


def run_preflight(
    *,
    local_home: Path,
    ollama_base_url: str,
    router_base_url: str,
    required_models: Iterable[str],
    doctor_runner: Callable[[Path, dict[str, str]], dict[str, Any]] | None = None,
) -> DiagnosticReport:
    report = DiagnosticReport()
    environment = {
        **os.environ,
        "CODEX_HOME": str(local_home),
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    try:
        version_response = httpx.get(f"{ollama_base_url}/api/version", timeout=3.0)
        version_response.raise_for_status()
        version = version_response.json().get("version")
        report.add("Ollama", bool(version), f"Version {version or '-'}")
    except (httpx.HTTPError, ValueError) as exc:
        report.add("Ollama", False, str(exc))

    try:
        tags_response = httpx.get(f"{ollama_base_url}/api/tags", timeout=5.0)
        tags_response.raise_for_status()
        models = tags_response.json().get("models", [])
        available = {
            str(item.get("name") or item.get("model"))
            for item in models
            if isinstance(item, dict)
        }
        missing = sorted(set(required_models) - available)
        report.add(
            "Lokale Modelle",
            not missing,
            "alle Aliase vorhanden" if not missing else f"fehlen: {', '.join(missing)}",
        )
    except (httpx.HTTPError, ValueError) as exc:
        report.add("Lokale Modelle", False, str(exc))

    try:
        health_response = httpx.get(f"{router_base_url}/health", timeout=4.0)
        health_response.raise_for_status()
        health = health_response.json()
        report.add(
            "Lokaler Router",
            health.get("status") == "ok",
            f"status={health.get('status', '-')}, pid={health.get('pid', '-')}"
        )
    except (httpx.HTTPError, ValueError) as exc:
        report.add("Lokaler Router", False, str(exc))

    try:
        model_response = httpx.get(f"{router_base_url}/v1/models", timeout=4.0)
        model_response.raise_for_status()
        entries = model_response.json().get("data", [])
        identities = {
            (item.get("id"), item.get("owned_by"))
            for item in entries
            if isinstance(item, dict)
        }
        local_only = identities == {("local-codex", "local")}
        report.add("Router-Modellkatalog", local_only, str(sorted(identities)))
    except (httpx.HTTPError, ValueError) as exc:
        report.add("Router-Modellkatalog", False, str(exc))

    try:
        payload = (doctor_runner or _doctor_payload)(local_home, environment)
        report.extend(inspect_codex_doctor(payload, local_home))
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        report.add("Codex Doctor", False, str(exc))
    return report


def format_report(report: DiagnosticReport) -> str:
    lines = []
    for check in report.checks:
        label = "OK" if check.ok else ("WARNUNG" if not check.required else "FEHLER")
        lines.append(f"[{label}] {check.name}: {check.detail}")
    return "\n".join(lines)
