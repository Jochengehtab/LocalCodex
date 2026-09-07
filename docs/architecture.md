# Architecture and decisions

## ADR 001: local modular application

Status: accepted. Keep Python/FastAPI, SQLite and SDL3/ImGui. No distributed scheduler,
multi-tenant server or new web UI. The launcher owns child processes; closing the monitor
must not interrupt inference. One inference is active per router to control local memory.

`app.py` contains transport endpoints and `create_app(runtime_factory)`. Each application
owns one runtime created in lifespan, with cleanup even after failed startup. Imports
must not create databases, network clients or tasks.

`service.py` orchestrates preparation, routing, local web-tool rounds, completion and usage.
It returns a transport-neutral `Result`; it has no FastAPI or SQLite dependency. Ollama's
buffered exchange is a protocol boundary, not end-to-end live token streaming to Codex.
`runtime.py` composes adapters and owns model switching and resource cleanup.

Routing and config are separate from transport. Storage adapters own SQLite. Telemetry is
bounded memory state observed through monitor snapshots/SSE; it does not start model calls.
The native `MonitorClient` hides HTTP/SSE and worker threads behind an implementation
boundary. The main monitor owns UI/lifecycle, while monitor state and system metrics have
independent native modules.

## ADR 002: explicit local configuration

Copy `examples/localcodex.toml` to `$LOCAL_CODEX_HOME/localcodex.toml`, edit installed model
sources and run `codex-local setup`, then restart the router. Defaults match existing
profiles. A role may share its source with another role; managed aliases remain distinct.
Configuration is read on process startup, not hot-reloaded.

Only schema version 1 and plan/build/vision tables are supported. Context is 2048–131072;
reasoning is none/low/medium/high/xhigh. Choose `none` for models without thinking support.
These are requested settings, not a promise that every model implements reasoning levels
identically. Setup validates Ollama capabilities. Model names cannot be URLs/cloud aliases.

Precedence for setup context: explicit `--context`, then model config if present, then
previous per-role benchmark/runtime context, then defaults. Explicit benchmark application
can set measured context sizes; rerunning setup with a config file reapplies that file.
The public Codex context is the minimum across active role profiles.

Routing priority: image, tool-error recovery, sticky session/turn, plan/review pattern,
implementation. Error detection remains heuristic. State keys include session and turn.
There are at most eight waiting requests plus the active request. The 900-second deadline
includes queue time; buffered model output is limited to 32 MiB per round. Completion,
usage and telemetry finalize before the active slot is released.

## ADR 003: stable boundaries and persistence

Existing CLI commands and HTTP routes remain. Responses and monitor JSON/SSE retain
their existing shapes. HTTP 429 means a full queue, 504 an overall deadline, 409 an
unavailable or foreign-session predecessor. Unsupported request tools return 400;
invalid model responses return 502. A closed HTTP client cancels inference processing.

SQLite schema 1 adopts existing tables without changing their shape. A legacy populated
database is backed up using SQLite's backup API before its version is recorded. A newer
unknown schema is refused. Future migrations must preserve rollback/recovery. Never
silently import workstation conversation data into a server or shared installation.

See [compatibility](compatibility.md), [privacy](../SECURITY.md) and
[contribution checks](../CONTRIBUTING.md).
