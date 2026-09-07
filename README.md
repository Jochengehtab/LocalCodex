# LocalCodex

**English:** This page · **Deutsch:** [README.de.md](README.de.md)

LocalCodex automatically routes planning, coding, image input and tool-error recovery to local
Ollama models while keeping the OpenAI Codex CLI workflow. Its Responses router manages model
aliases, offers optional SearXNG web search, and exposes
session and historical usage statistics through a native Dear ImGui monitor.

Codex already supports Ollama directly. LocalCodex adds automatic role-based model switching,
managed model lifecycles and a native monitor. See the [comparison and compatibility limits](docs/compatibility.md).
This is an independent community project, licensed under [AGPL-3.0-only](LICENSE).

**Start here:** [model configuration](examples/localcodex.toml) · [architecture](docs/architecture.md) ·
[contributing](CONTRIBUTING.md) · [benchmark protocol](examples/benchmarks/README.md).
A [real local verification run](docs/verification.md) covers Codex/Ollama inference and a
three-role smoke benchmark. A public video and the full comparative benchmark are pending;
no quality or speed advantage is claimed.

> The workflow follows Codex, but model quality depends on the installed local Qwen models and
> available hardware.

## Features

- Local-only provider on `127.0.0.1`; cloud model overrides are blocked.
- Automatic routing between `qwen3.8:27b`, `qwen3.6:35b-a3b`, and `qwen3-vl:30b`.
- Model sources, context and reasoning settings are configurable per role; reasoning summaries are never stored.
- Optional web search through local SearXNG; queries still reach external search engines.
- Exact Ollama usage per Codex thread plus 24h/7d/30d/all-time statistics.
- Native Windows x64 and Linux x64 monitor built with SDL3, Dear ImGui, and ImPlot.
- Only LocalCodex Ollama aliases are unloaded after the final session; unrelated models stay loaded.
- SemVer releases, SHA-256 verification, atomic updates, rollback, and uninstall.
- German and English localization via `LOCAL_CODEX_LANGUAGE=de|en` or Monitor → Settings → Language;
  English is the default.

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
input/output tokens, savings, and tokens/s are estimated from the existing request and stream;
after completion Ollama's exact usage replaces the estimate. History can be filtered by session
and period and exported as CSV or JSON.

API endpoints:

- `GET /monitor/snapshot` — live state, active launchers, and current session.
- `GET /monitor/events` — coalesced live SSE updates (maximum 4 Hz).
- `GET /monitor/statistics` — periods, sessions, models, and pagination.
- `GET /monitor/statistics/export` — CSV/JSON raw data.

The dashboard uses a persistent local SSE stream for live updates and falls back to low-frequency
polling when necessary. It shows turn, session, and all-time tokens and savings, TTFT, current and
average throughput, context utilization, Codex mode and phase, the routed alias, source model, and
the latest tool. The throughput graph has last-120-seconds, current-turn, and full-session views;
both axes scale to the selected range and peak-preserving compaction keeps the buffer bounded.

CPU and RAM usage come from native operating-system APIs. GPU load and global VRAM usage use NVML
when available, with Windows PDH/DXGI and Linux DRM sysfs as best-effort fallbacks. A separate model
allocation line continues to use Ollama's `/api/ps` values. Unsupported metrics are shown as
`Not available` instead of starting helper processes. Resource sampling runs once per second while
the window is visible and once every five seconds while hidden.

## Context benchmark

Run the local benchmark without changing the active aliases:

```bash
codex-local benchmark
```

It tests 8K through 128K independently for the planning, build, and vision models, stores a JSON
report, and asks before applying the recommended stable window for each model. Use `--json` for
machine-readable output or `--apply` for an explicit non-interactive application.

## Creating a release

Open **Actions → Release → Run workflow**, enter a semantic version without the `v` prefix, and
select whether it is a prerelease. The workflow commits `VERSION`, runs the complete Linux/Windows
matrix, creates checksummed and attested artifacts, tags the tested commit, and publishes the
GitHub release.
Repository Actions must have `contents: write`; protected branches must allow the GitHub Actions
bot to create the release version commit.

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

See [README.local-codex.md](README.local-codex.md) for the German routing, web search, context
benchmarking, diagnostics, and architecture details.

Contribution and agent rules are documented in [AGENTS.md](AGENTS.md).
