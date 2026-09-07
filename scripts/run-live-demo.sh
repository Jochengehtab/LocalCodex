#!/usr/bin/env bash
set -euo pipefail

repository="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repository"

if [[ ! -x .venv/bin/python ]]; then
  echo "LocalCodex: .venv/bin/python is required." >&2
  exit 2
fi

exec .venv/bin/python start_codex.py --self-test --json
