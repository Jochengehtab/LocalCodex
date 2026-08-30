# LocalCodex

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
Live-Ausgabe und Tokens/s werden während eines Turns aus dem vorhandenen Stream geschätzt; nach
Abschluss ersetzen Ollamas exakte Usage-Werte die Schätzung. Die Historie kann nach Sitzung und
Zeitraum gefiltert sowie als CSV oder JSON exportiert werden.

API-Endpunkte:

- `GET /monitor/snapshot` – Livezustand, aktive Launcher und aktuelle Sitzung.
- `GET /monitor/statistics` – Zeiträume, Sitzungen, Modelle und Pagination.
- `GET /monitor/statistics/export` – CSV-/JSON-Rohdaten.

Beim Ende der letzten Launcher-Lease entlädt der Router nur
`local-codex-plan:latest`, `local-codex-build:latest` und
`local-codex-vision:latest`. Andere Ollama-Modelle bleiben unangetastet.

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
