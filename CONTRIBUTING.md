# Contributing to LocalCodex

LocalCodex is a local application: Python owns the Codex/Ollama lifecycle and a native
C++ monitor observes it. Read [AGENTS.md](AGENTS.md) and [architecture](docs/architecture.md).
Contributions to original project code are under AGPL-3.0-only; retain third-party notices.

## Development

Use Python 3.12+, a virtual environment and the dependencies in
`requirements-local-codex.txt`. Developer checks use `requirements-dev.txt`.
Run focused unit tests first, then the commands in AGENTS.md. Tests normally use fake
model transports; never enable real-model tests or download models implicitly.

Run `python -m ruff check .`, `python -m mypy` and
`python -m unittest discover -s tests -v`. Native monitor changes need Linux and Windows
builds. Distinguish cross-compilation from actually running Windows tests.

The installer is the supported distribution mechanism; pyproject.toml configures quality
tools and does not introduce a second Python-package installation workflow.

## Reviewable changes

Use focused Conventional Commit messages. Describe the concrete before/after behavior,
the validation you ran and any compatibility limit. Separate a behavior change from
unrelated formatting. Preserve existing local edits. Do not commit generated artifacts,
models, credentials, runtime databases, screenshots of private code or reasoning content.

New UI text belongs in Python `i18n.tr()` or the monitor's i18n table, with English and
German translations. New API fields must preserve existing monitor consumers.

## Small first contributions

1. Add an anonymized hardware report using the benchmark report template, including failures.
2. Add one isolated fixture for a currently unsupported tool shape; describe expected behavior
   before changing the adapter. Unknown tools must never become shell commands.
3. Improve one installation troubleshooting case in both README languages and verify it
   on the stated OS/version.

Use [bug reports](https://github.com/Jochengehtab/LocalCodex/issues/new?template=bug_report.yml)
for reproducible issues. Report sensitive security issues through [SECURITY.md](SECURITY.md).
