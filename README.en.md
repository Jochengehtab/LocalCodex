# LocalCodex

**English:** This page · **Deutsch:** [README.md](README.md)

LocalCodex connects the real OpenAI Codex CLI to Ollama locally. A local Responses router selects
Qwen planning, coding, and vision models, provides private SearXNG web search, and exposes exact
session and historical usage statistics through a native Dear ImGui monitor.

> The workflow follows Codex, but model quality depends on the installed local Qwen models and
> available hardware.

## Features

- Local-only provider on `127.0.0.1`; cloud model overrides are blocked.
- Automatic routing between `qwen3.8:27b`, `qwen3.6:35b-a3b`, and `qwen3-vl:30b`.
- New turns use the maximum `xhigh` reasoning level; raw reasoning summaries are never stored.
- Free current web search through a local SearXNG container.
- Exact Ollama usage per Codex thread plus 24h/7d/30d/all-time statistics.
- Native Windows x64 and Linux x64 monitor built with SDL3, Dear ImGui, and ImPlot.
- Only LocalCodex Ollama aliases are unloaded after the final session; unrelated models stay loaded.
- SemVer releases, SHA-256 verification, atomic updates, rollback, and uninstall.
- German and English localization via `LOCAL_CODEX_LANGUAGE=de|en` or Monitor → Settings → Language.

## Installation

### Windows 10/11

The Windows installer uses WSL2 for the router and Codex and installs the monitor as a native
Windows application. Missing prerequisites and large model downloads are confirmed first.

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Jochengehtab/LocalCodex/main/install.ps1 -OutFile install-localcodex.ps1
powershell -ExecutionPolicy Bypass -File .\install-localcodex.ps1
```

### Linux x64

```bash
curl -fsSLo install-localcodex.sh https://raw.githubusercontent.com/Jochengehtab/LocalCodex/main/install.sh
bash install-localcodex.sh
codex-local
```

Switch language without reinstalling:

```bash
LOCAL_CODEX_LANGUAGE=en codex-local
```

Useful commands:

```bash
codex-local doctor
codex-local self-test
codex-local usage --all
codex-local update
codex-local rollback
codex-local uninstall
codex-local uninstall --purge-data
```

## Monitor and statistics

The monitor reads only local endpoints and never starts a second model request. During a turn,
output tokens and tokens/s are estimated from the existing stream; after completion Ollama's exact
usage replaces the estimate. History can be filtered by session and period and exported as CSV or
JSON.

API endpoints:

- `GET /monitor/snapshot` — live state, active launchers, and current session.
- `GET /monitor/statistics` — periods, sessions, models, and pagination.
- `GET /monitor/statistics/export` — CSV/JSON raw data.

## Development

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-local-codex.txt
./.venv/bin/python -m unittest discover -s tests -v

cmake -S . -B build/native -DCMAKE_BUILD_TYPE=Release
cmake --build build/native --parallel
ctest --test-dir build/native --output-on-failure
```

The monitor is built during setup only when explicitly requested:

```bash
./.venv/bin/python start_codex.py setup --build-monitor
```

See [README.local-codex.md](README.local-codex.md) for routing, web search, context benchmarking,
diagnostics, and architecture details.

Contribution and agent rules are documented in [AGENTS.md](AGENTS.md).
