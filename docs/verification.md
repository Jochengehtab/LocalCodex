# Local verification record

Measured on 2026-09-07 on an AMD Ryzen 9 7900 (12 cores/24 threads), 23 GiB
usable RAM and WSL2/Linux. Codex CLI was 0.153.4 and the Ollama client/server was
0.32.15. GPU/NVML metrics were unavailable in this WSL environment, so no VRAM or
GPU claim is made.

## End-to-end compatibility

Both opt-in tests used the real installed Codex CLI, the loopback-only LocalCodex
router and real local Ollama models. They were not mocked:

- non-interactive `codex exec`: passed in 43.856 seconds;
- interactive Codex TUI through a pseudo-terminal: passed in 58.003 seconds;
- self-test: 11 required checks passed, including cloud lockout, real inference,
  telemetry and exact managed-model detection;
- observed route: `local-codex-build:latest`, 3,713 input and 59 output tokens.

The native monitor test passed on Linux. The Windows x64 binary and its tests
cross-compiled successfully, but could not execute in this WSL instance because
Windows binary interoperability is disabled (`Exec format error`). This is build
verification, not a Windows runtime claim.

## 8K role smoke benchmark

Each role ran once against the real source model. The test checks one correct tool
call for plan/build and the expected image answer for vision. These samples validate
the path; one repetition is not a statistically useful performance comparison.

| Role | Source model | Result | Wall time | Minimum available RAM | Maximum swap |
| --- | --- | ---: | ---: | ---: | ---: |
| Plan | qwen3.8:27b | 1/1 | 35.022 s | 22,291,116,032 B | 2,760,704 B |
| Build | qwen3.6:35b-a3b | 1/1 | 31.243 s | 22,649,446,400 B | 4,784,128 B |
| Vision | qwen3-vl:30b | 1/1 | 36.923 s | 23,181,234,176 B | 20,951,040 B |

Ollama did not expose throughput, loaded-model size or VRAM for these probes; those
values remain unavailable rather than being reported as zero. The complete 72-run
comparative protocol in [the benchmark guide](../examples/benchmarks/README.md) is
still required before publishing model-quality or performance comparisons.

## Reproduce the live demo

The following command is the local executable demo. It validates setup, runs a real
Codex request, verifies telemetry and shuts the router down cleanly:

```bash
./scripts/run-live-demo.sh
```

For a public video, use the 90-second script in [visibility.md](visibility.md), show
the monitor, disclose cuts and retain the real wait times above.
