# Reproducible smoke benchmark

These twelve small tasks check routing and basic harness behavior, not general coding
intelligence. No measured results are currently published. Do not infer a hardware
recommendation from the default model list or CI results.

For every task, create a fresh disposable directory containing `bench_project.py` and
`checks.py`; initialize git there so unintended edits are visible. Never use your working
repository as the benchmark workspace. The fixture is intentionally broken; its acceptance
tests are only run for the task being evaluated, not as repository-wide regression tests.

Use the exact prompt in `tasks.json`. For the three vision tasks, render `routing.svg` to
a PNG using your normal SVG viewer/exporter and attach that PNG. Keep the SVG out of the
agent workspace so reading source cannot replace image understanding. Record the renderer
and PNG checksum in the report. Do not claim a vision result from text-only execution.

Run each task three times through LocalCodex and three times with direct Codex+Ollama:
72 runs total. Use the same host, source models, context, temperature and reasoning
settings. Alternate which mode runs first. Direct mode uses the role's source model;
recovery tasks start on the build model in both modes. Disable web search for these tasks.
Record any inability to equalize settings instead of hiding it.

Direct command: `codex --oss --local-provider ollama -m MODEL`.
Routed command: `codex-local --no-monitor` (open the monitor separately for recording).
Use a new session for each run and retain only sanitized visible answers, diffs and
acceptance-test output. Do not export reasoning, private code or runtime databases.

Record per run: task id, mode, repetition, versions, source model digests/quantization,
RAM/VRAM, context and requested reasoning, cold/warm state, pass/fail against the rubric,
correct tool usage, wall-clock duration, observed model switches and peak memory. Mark
unavailable measurements as null; never replace them with zeros. Separate model loading
from inference when the backend exposes timings. Report hardware sampling method/interval.

Use [report-template.md](report-template.md) to publish aggregate results and failures.
Screenshots or recordings must show real execution and label cuts/waits. Compare role
routing independently from model quality: selecting the expected alias is not proof
that the generated code is correct.
