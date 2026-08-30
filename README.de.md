# LocalCodex

**English:** [README.md](README.md) · **Deutsch:** Diese Seite

LocalCodex verbindet die echte OpenAI Codex CLI vollständig lokal mit Ollama. Ein lokaler
Responses-Router wählt automatisch zwischen Qwen-Planungs-, Coding- und Vision-Modellen, stellt
eine private SearXNG-Websuche bereit und liefert exakte Sitzungs- und Langzeitstatistiken an einen
nativen Dear-ImGui-Monitor.

> Die Bedienung entspricht dem Codex-Workflow, die Modellqualität ist jedoch von den lokal
> installierten Qwen-Modellen und der verfügbaren Hardware abhängig.

## Funktionen

- Lokaler Codex-Provider auf `127.0.0.1`; Cloud-Modell-Overrides werden blockiert.
- Automatisches Routing zwischen `qwen3.8:27b`, `qwen3.6:35b-a3b` und `qwen3-vl:30b`.
- Maximaler `xhigh`-Reasoning-Level, ohne Rohgedanken oder Denkzusammenfassungen zu speichern.
- Kostenlose aktuelle Websuche über einen lokalen SearXNG-Container.
- Exakte Ollama-Usage-Werte pro Codex-Thread sowie 24h-/7d-/30d-/Gesamtstatistiken.
- Native Windows-x64- und Linux-x64-Oberfläche mit SDL3, Dear ImGui und ImPlot.
- Automatisches Entladen ausschließlich der LocalCodex-Ollama-Aliase nach der letzten Sitzung.
- SemVer-Releases, SHA-256-Prüfung, atomare Updates und Rollback.
- Vollständige Deutsch-/Englisch-Lokalisierung per `LOCAL_CODEX_LANGUAGE=de|en` oder im
  Monitor unter Einstellungen → Sprache. Englisch ist standardmäßig aktiv; unbekannte Werte
  fallen sicher auf Englisch zurück.

## Installation

### Windows 10/11

Das Windows-Setup verwendet WSL2 für Router und Codex und installiert den Monitor als native
Windows-Anwendung. Fehlende Voraussetzungen und die großen Modell-Downloads werden vor der
Installation bestätigt.

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Jochengehtab/LocalCodex/main/install.ps1 -OutFile install-localcodex.ps1
powershell -ExecutionPolicy Bypass -File .\install-localcodex.ps1
```

Wenn WSL erstmals aktiviert werden muss, kann ein Neustart erforderlich sein. Derselbe Befehl
setzt die idempotente Installation danach fort.

### Linux x64

```bash
curl -fsSLo install-localcodex.sh https://raw.githubusercontent.com/Jochengehtab/LocalCodex/main/install.sh
bash install-localcodex.sh
```

Danach sollte `~/.local/bin` im `PATH` liegen:

```bash
codex-local
```

Die Sprache lässt sich ohne Neuinstallation umschalten:

```bash
LOCAL_CODEX_LANGUAGE=en codex-local
```

Nützliche Verwaltungsbefehle:

```bash
codex-local doctor
codex-local self-test
codex-local usage --all
codex-local update
codex-local rollback
codex-local uninstall
codex-local uninstall --purge-data
```

Installierte Releases prüfen beim Start höchstens einmal pro 24 Stunden auf eine neue stabile
Version. Aktualisiert wird nur nach ausdrücklicher Auswahl. Entwickler-Clones führen keine
automatische Updateprüfung aus.

## Monitor und Statistik

Der Monitor liest ausschließlich lokale Endpunkte und führt keine zweite Modellanfrage aus.
Input-/Output-Tokens, Ersparnis und Tokens/s werden während eines Turns aus der bestehenden
Anfrage und dem vorhandenen Stream geschätzt; nach Abschluss ersetzen Ollamas exakte Usage-Werte
die Schätzung. Die Historie kann nach Sitzung und Zeitraum gefiltert sowie als CSV oder JSON
exportiert werden.

API-Endpunkte:

- `GET /monitor/snapshot` – Livezustand, aktive Launcher und aktuelle Sitzung.
- `GET /monitor/events` – gebündelte Live-SSE-Updates (maximal 4 Hz).
- `GET /monitor/statistics` – Zeiträume, Sitzungen, Modelle und Pagination.
- `GET /monitor/statistics/export` – CSV-/JSON-Rohdaten.

Das Dashboard empfängt Livewerte über einen persistenten lokalen SSE-Stream und nutzt nur bei
Bedarf niedrigfrequentes Polling. Es zeigt Turn-, Sitzungs- und Gesamt-Tokens/Ersparnis, TTFT,
Tokens/s, Kontextauslastung, Modellrolle, Ollama-RAM/VRAM, Phase und das letzte Werkzeug. Der
Tokens/s-Graph skaliert automatisch über die gesamte Sitzung und erhält Spitzen in einem
begrenzten Speicher.

Beim Ende der letzten Launcher-Lease entlädt der Router nur
`local-codex-plan:latest`, `local-codex-build:latest` und
`local-codex-vision:latest`. Andere Ollama-Modelle bleiben unangetastet.

## Kontextbenchmark

```bash
codex-local benchmark
```

Der Benchmark prüft 8K bis 128K unabhängig für Plan-, Build- und Vision-Modell, speichert einen
JSON-Bericht und fragt vor der Übernahme des empfohlenen stabilen Fensters pro Modell nach. Für
automatisierbare Ausgaben stehen `--json` und für eine ausdrückliche Übernahme `--apply` bereit.

## Release erstellen

Unter **Actions → Release → Run workflow** werden Version und Stable/Prerelease gewählt. Der
Workflow committet `VERSION`, führt die Linux-/Windows-Matrix aus, erzeugt geprüfte Artefakte und
veröffentlicht Tag und GitHub-Release für exakt den getesteten Commit.
Repository-Actions benötigen `contents: write`; bei geschütztem `main` muss der GitHub-Actions-Bot
den Versionscommit erstellen dürfen.

## Entwicklung

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-local-codex.txt
./.venv/bin/python -m unittest discover -s tests -v

cmake -S . -B build/native -DCMAKE_BUILD_TYPE=Release
cmake --build build/native --parallel
ctest --test-dir build/native --output-on-failure
```

Ein lokaler Setup-Lauf baut den Monitor nur auf ausdrücklichen Wunsch:

```bash
./.venv/bin/python start_codex.py setup --build-monitor
```

Die CMake-Abhängigkeiten sind auf konkrete Archive und SHA-256-Hashes festgelegt. GitHub Actions
baut und testet Python und C++ unter Linux und Windows, prüft beide Installer und veröffentlicht
nur validierte `vX.Y.Z`-Tags als GitHub Release. Release-Assets enthalten keine Modelle.

Weitere Details zu Routing, Websuche, Kontextbenchmark und Diagnose stehen in
[README.local-codex.md](README.local-codex.md).

Beitrags- und Agentenregeln stehen in [AGENTS.md](AGENTS.md); die englische Dokumentation ist
[README.md](README.md).
