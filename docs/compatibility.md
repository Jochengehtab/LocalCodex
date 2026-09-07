# Compatibility and comparisons

Documentation/source review: 2026-09-07. Comparison projects were inspected, not benchmarked.

| Capability | Codex + Ollama directly | LocalCodex | opencodex | codex-model-router |
| --- | --- | --- | --- | --- |
| Use models through Codex | Yes | Yes | Yes | Yes |
| Local Ollama | Yes | Required | Documented provider | Not established by reviewed example |
| Plan/code/image/error routing | Not established by CLI flags | Explicit heuristic rules | Not established by reviewed routing section | Exact model-name routes |
| Native LocalCodex usage monitor | No | Yes | Not claimed here | Not claimed here |

Sources: [Codex CLI](https://learn.chatgpt.com/docs/developer-commands?surface=cli),
[opencodex](https://github.com/Ding-Ding-Projects/opencodex#model-routing),
[codex-model-router](https://github.com/ustas-eth/codex-model-router),
[Ollama model metadata](https://docs.ollama.com/api-reference/show-model-details).
This is not a claim of uniqueness or higher model quality.

## Contract supported by this adapter

- Responses function tools, including known shell/search/patch aliases; arbitrary unknown
  names never fall back to shell execution.
- Host instructions and skill/plugin instruction text are preserved. Custom, namespace,
  deferred and provider-specific tool schemas are not yet supported and fail explicitly.
  Preserving instructions does not imply support for every Codex plugin/app capability.
- Conversation continuation requires a live predecessor belonging to the same session.
- Upstream model SSE updates monitor telemetry. Codex receives a normalized **buffered**
  completed response. Full token streaming is not advertised.
- Reasoning events are discarded; token estimates are replaced with native completed usage.

The pre-existing implementation targeted Codex 0.151 behavior. This refactor requires a
fresh live compatibility run before declaring a specific CLI/Ollama/model combination
supported. Unit/transport fixtures are not a substitute for that run. Record exact CLI,
Ollama, source model digest, quantization and context in a benchmark report. Do not use
an unversioned “works with all Codex versions” badge.
