# Launch kit

## Repository metadata

Description: Automatic plan/code/vision routing for local Ollama models in the Codex CLI,
with managed model switching and a native usage monitor.

Topics: codex, ollama, local-llm, model-routing, coding-agent, self-hosted.
Enable private vulnerability reporting and Discussions in GitHub settings. These external
settings and public posts are maintainer actions; this document does not apply them.

## 90-second recording script

Use a public disposable example project, hide private paths and keep the monitor visible.
0–15s: state hardware, model names and the problem of manually switching local models.
15–35s: request a plan; show the selected planning alias.
35–60s: request implementation and run the example's tests; show the coding alias.
60–80s: attach a public diagram/screenshot; show the vision alias.
80–90s: show the install link and candid limitations. Label cuts, show actual waiting
times and do not substitute mocked output for real inference. Link benchmark results.

No recording or performance result is bundled yet. Capture after live compatibility checks.

## Post draft

Title: LocalCodex: automatic plan/code/vision routing with local models in the Codex CLI

I maintain LocalCodex, an AGPLv3 project that selects local Ollama models for planning,
implementation and image inputs while keeping the Codex CLI workflow. It also manages
its own model aliases and provides a native usage monitor on Linux and Windows.

Codex already supports Ollama directly. The goal here is to make automatic role-based
switching and local usage visibility easier. The current router is heuristic; model
quality and switching latency depend on hardware. The Responses adapter supports a
documented subset of tools, and client responses are currently buffered.

Repository: https://github.com/Jochengehtab/LocalCodex
Before publishing, attach a real demo, exact hardware details and the benchmark report.
Feedback sought: installation issues, incorrect routing and missing tool compatibility.

## Publishing and follow-up

First get three external installation reports using only the README. Fix blockers before
announcing. Adapt the draft for r/LocalLLaMA, r/ollama and Show HN after reading their current
rules; disclose authorship and answer substantive questions. Do not mass-post identical
advertisements. Publish a concrete progress update after two weeks.

Capture a baseline, then review weekly for four weeks: GitHub unique visitors, release
downloads, voluntary successful-install reports and returning feedback. Stars are a
secondary signal; downloads do not prove active usage. Do not collect hidden telemetry.

Readiness checklist: license and source archives; passing release checks; real recording;
hardware/compatibility report; three installation reports; working private security channel.
