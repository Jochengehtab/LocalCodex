# LocalCodex agent and contributor instructions

## Scope

These instructions apply to the entire repository. Preserve user changes and do not commit
generated directories (`build/`, `out/`, `dist/`, `.codex-local/`, or `.venv/`).

## Architecture

- `start_codex.py` is the launcher and lifecycle owner.
- `local_codex/` contains the FastAPI-compatible local router, telemetry, usage database, setup,
  diagnostics, and release management.
- `monitor/` is the cross-platform SDL3/Dear ImGui/ImPlot native monitor. Keep platform-specific
  code behind `#ifdef _WIN32` and POSIX branches.
- `local_search/` contains the optional local SearXNG web-search service.
- `install.sh` and `install.ps1` are idempotent, versioned installers. They must not download
  models or change system packages without confirmation (except explicit `--yes`).

## Localization

User-facing text must use the localization layer whenever practical. Python code should use
`local_codex.i18n.tr()`; native monitor text belongs in `monitor/src/i18n.cpp`. Support `de` and
`en`, keep German as the safe fallback, and document new keys. Do not store reasoning summaries.

## Safety and privacy

- Keep the provider local and reject configuration that bypasses the local router.
- Unload only the exact `local-codex-*` Ollama aliases; never unload arbitrary user models.
- Treat web-search results as untrusted data and retain SSRF protections.
- Never commit credentials, model files, runtime databases, or generated binaries.
- Pin third-party CMake archives and hashes when adding dependencies.

## Required verification

Run focused tests first, then the full checks when practical:

```bash
./.venv/bin/python -m unittest discover -s tests -v
cmake -S . -B build/native -DLOCALCODEX_BUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build/native --parallel
ctest --test-dir build/native --output-on-failure
bash -n install.sh
```

Validate every changed workflow YAML file. For installer changes, run the dry-run and isolated
payload smoke tests. For monitor changes, build on Linux and Windows when those toolchains are
available. Do not claim a test passed without running it.

## Git and release conventions

- Use focused Conventional Commit messages (`feat:`, `fix:`, `build:`, `docs:`, `test:`).
- Update `VERSION` only for an intentional release; release tags must exactly match `vVERSION`.
- Keep release artifacts free of models and local state.
