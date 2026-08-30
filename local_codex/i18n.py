"""Small, dependency-free localization layer shared by the Python entry points.

The language can be selected with ``LOCAL_CODEX_LANGUAGE=de|en``. English is
the deterministic default regardless of the host operating system locale.
"""
from __future__ import annotations

import os
from typing import Final

SUPPORTED_LANGUAGES: Final = ("de", "en")

_MESSAGES: Final[dict[str, dict[str, str]]] = {
    "de": {
        "release.none": "Kein stabiles GitHub-Release gefunden.",
        "release.rollback_only": "Rollback ist nur für eine Release-Installation verfügbar.",
        "release.no_previous": "Keine ältere installierte Version gefunden.",
        "release.uninstall_only": "Uninstall ist nur für eine Release-Installation verfügbar.",
        "release.unsafe_root": "Unsicheres Installationsziel; Abbruch.",
        "release.remove_prompt": "LocalCodex-Programme aus {root} entfernen? [j/N] ",
        "release.removed": "Programme entfernt.",
        "release.stats_removed": "Statistiken gelöscht.",
        "release.stats_kept": "Statistiken bleiben erhalten.",
        "release.available": "Neue Version {version} verfügbar (installiert: {current}).",
        "release.update_prompt": "Jetzt aktualisieren [j], diesmal überspringen [Enter], Version ignorieren [i]? ",
        "release.updated": "Update installiert. Bitte LocalCodex erneut starten.",
        "release.failed": "Update fehlgeschlagen; die aktive Version wurde nicht verändert.",
        "status.ok": "OK",
        "status.warning": "WARNUNG",
        "status.error": "FEHLER",
        "session.untitled": "Sitzung {id}",
        "setup.missing_models": "Fehlende Ollama-Modelle: {models}",
        "setup.benchmark_active": "Beende aktive LocalCodex-Sitzungen, bevor du den Kontextbenchmark startest.",
        "setup.benchmark_result": "{context}K {role}: {successes}/{repetitions} {measurement}, Median {seconds:.1f}s",
        "setup.tool_calls": "Werkzeugaufrufe",
        "setup.vision_answers": "Bildantworten",
        "setup.apply_contexts": "Diese Kontextfenster übernehmen? [j/N] ",
        "setup.recommendations": "Empfohlene Kontextfenster:",
        "setup.report": "Bericht: {path}",
        "setup.applied": "Kontextempfehlungen übernommen.",
        "setup.config_updated": "LocalCodex-Konfiguration aktualisiert: {context} Kontext-Tokens",
        "setup.installed": "LocalCodex eingerichtet: {context} Kontext-Tokens",
    },
    "en": {
        "release.none": "No stable GitHub release found.",
        "release.rollback_only": "Rollback is only available for a release installation.",
        "release.no_previous": "No older installed version found.",
        "release.uninstall_only": "Uninstall is only available for a release installation.",
        "release.unsafe_root": "Unsafe installation target; aborting.",
        "release.remove_prompt": "Remove LocalCodex programs from {root}? [y/N] ",
        "release.removed": "Programs removed.",
        "release.stats_removed": "Statistics deleted.",
        "release.stats_kept": "Statistics were kept.",
        "release.available": "New version {version} available (installed: {current}).",
        "release.update_prompt": "Update now [y], skip this time [Enter], ignore version [i]? ",
        "release.updated": "Update installed. Please restart LocalCodex.",
        "release.failed": "Update failed; the active version was not changed.",
        "status.ok": "OK",
        "status.warning": "WARNING",
        "status.error": "ERROR",
        "session.untitled": "Session {id}",
        "setup.missing_models": "Missing Ollama models: {models}",
        "setup.benchmark_active": "Stop active LocalCodex sessions before running the context benchmark.",
        "setup.benchmark_result": "{context}K {role}: {successes}/{repetitions} {measurement}, median {seconds:.1f}s",
        "setup.tool_calls": "tool calls",
        "setup.vision_answers": "vision answers",
        "setup.apply_contexts": "Apply these context windows? [y/N] ",
        "setup.recommendations": "Recommended context windows:",
        "setup.report": "Report: {path}",
        "setup.applied": "Context recommendations applied.",
        "setup.config_updated": "LocalCodex configuration updated: {context} context tokens",
        "setup.installed": "LocalCodex configured: {context} context tokens",
    },
}


def _detect_language() -> str:
    requested = os.environ.get("LOCAL_CODEX_LANGUAGE", "").strip().lower().replace("_", "-")
    if requested:
        return "de" if requested.startswith("de") else "en"
    return "en"


_language = _detect_language()


def get_language() -> str:
    return _language


def set_language(value: str) -> str:
    """Set and return a supported language; unknown values fall back to English."""
    global _language
    _language = "de" if value.lower().startswith("de") else "en"
    return _language


def tr(key: str, **values: object) -> str:
    message = _MESSAGES.get(_language, {}).get(key) or _MESSAGES["en"].get(key) or key
    return message.format(**values)
