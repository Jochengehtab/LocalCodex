#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="Jochengehtab/LocalCodex"
INSTALL_ROOT="${LOCAL_CODEX_INSTALL_ROOT:-$HOME/.local/share/localcodex}"
DATA_ROOT="${LOCAL_CODEX_HOME:-$INSTALL_ROOT/data}"
BIN_DIR="${LOCAL_CODEX_BIN_DIR:-$HOME/.local/bin}"
VERSION_REQUEST=""
ASSUME_YES=0
SKIP_MODELS=0
SKIP_SEARCH=0
DRY_RUN=0
UPDATE=0
SKIP_SETUP=0

for argument in "$@"; do
  case "$argument" in
    --yes) ASSUME_YES=1 ;;
    --skip-models) SKIP_MODELS=1 ;;
    --skip-search) SKIP_SEARCH=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --update) UPDATE=1 ;;
    --skip-setup) SKIP_SETUP=1 ;;
  esac
done
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION_REQUEST="$2"; shift 2 ;;
    --yes|--skip-models|--skip-search|--dry-run|--update|--skip-setup|--from-payload) shift ;;
    *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
  esac
done

confirm() {
  [[ "$ASSUME_YES" == 1 ]] && return 0
  read -r -p "$1 [j/N] " answer
  [[ "$answer" =~ ^([jJ]|[jJ][aA]|[yY]|[yY][eE][sS])$ ]]
}
run() { if [[ "$DRY_RUN" == 1 ]]; then printf '[dry-run]'; printf ' %q' "$@"; echo; else "$@"; fi; }

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -d "$SCRIPT_ROOT/local_codex" ]]; then
  command -v curl >/dev/null || { echo "curl wird für den Bootstrap benötigt." >&2; exit 1; }
  if [[ -z "$VERSION_REQUEST" ]]; then
    VERSION_REQUEST="$(curl -fsSL "https://api.github.com/repos/$REPOSITORY/releases/latest" | sed -n 's/.*"tag_name": "v\([^"]*\)".*/\1/p' | head -1)"
  fi
  [[ -n "$VERSION_REQUEST" ]] || { echo "Kein stabiles Release gefunden." >&2; exit 1; }
  bootstrap_dir="$(mktemp -d)"
  trap 'rm -rf "$bootstrap_dir"' EXIT
  base="https://github.com/$REPOSITORY/releases/download/v$VERSION_REQUEST"
  curl -fL "$base/localcodex-core-v$VERSION_REQUEST.tar.gz" -o "$bootstrap_dir/core.tar.gz"
  curl -fL "$base/SHA256SUMS" -o "$bootstrap_dir/SHA256SUMS"
  expected="$(awk '/localcodex-core-v'"$VERSION_REQUEST"'\.tar\.gz$/ {print $1}' "$bootstrap_dir/SHA256SUMS")"
  [[ -n "$expected" && "$(sha256sum "$bootstrap_dir/core.tar.gz" | awk '{print $1}')" == "$expected" ]] || { echo "Checksum-Prüfung fehlgeschlagen." >&2; exit 1; }
  mkdir -p "$bootstrap_dir/core"
  tar -xzf "$bootstrap_dir/core.tar.gz" -C "$bootstrap_dir/core"
  exec "$bootstrap_dir/core/install.sh" --from-payload --version "$VERSION_REQUEST" $([[ "$ASSUME_YES" == 1 ]] && echo --yes) $([[ "$SKIP_MODELS" == 1 ]] && echo --skip-models) $([[ "$SKIP_SEARCH" == 1 ]] && echo --skip-search) $([[ "$UPDATE" == 1 ]] && echo --update) $([[ "$SKIP_SETUP" == 1 ]] && echo --skip-setup)
fi

VERSION="${VERSION_REQUEST:-$(tr -d '[:space:]' < "$SCRIPT_ROOT/VERSION")}" 
TARGET="$INSTALL_ROOT/versions/v$VERSION"

if [[ "$DRY_RUN" == 1 ]]; then
  echo "Würde LocalCodex $VERSION nach $TARGET installieren."
  exit 0
fi

if [[ "$SKIP_SETUP" == 0 ]] && { ! command -v python3 >/dev/null || ! python3 -m venv --help >/dev/null 2>&1; }; then
  if command -v apt-get >/dev/null && confirm "Python, venv und Build-Werkzeuge automatisch installieren?"; then
    run sudo apt-get update
    run sudo apt-get install -y python3 python3-venv curl ca-certificates
  else
    echo "Python 3 mit venv fehlt." >&2; exit 1
  fi
fi
if [[ "$SKIP_SETUP" == 0 ]] && ! command -v ollama >/dev/null; then
  if confirm "Ollama über den offiziellen Installer installieren?"; then
    curl -fsSL https://ollama.com/install.sh -o /tmp/localcodex-ollama-install.sh
    run sh /tmp/localcodex-ollama-install.sh
  else echo "Ollama fehlt." >&2; exit 1; fi
fi
if [[ "$SKIP_SETUP" == 0 ]] && ! command -v codex >/dev/null; then
  if ! command -v npm >/dev/null && command -v apt-get >/dev/null && confirm "Node.js und npm für die Codex CLI installieren?"; then
    run sudo apt-get update
    run sudo apt-get install -y nodejs npm
  fi
  if command -v npm >/dev/null && confirm "OpenAI Codex CLI automatisch über npm installieren?"; then
    run npm install -g @openai/codex
  else echo "Codex CLI oder npm fehlt." >&2; exit 1; fi
fi

if [[ "$SKIP_SETUP" == 0 && "$SKIP_SEARCH" == 0 ]] && ! command -v docker >/dev/null; then
  if command -v apt-get >/dev/null && confirm "Docker und Docker Compose für die lokale Websuche installieren?"; then
    run sudo apt-get update
    run sudo apt-get install -y docker.io docker-compose-v2
    run sudo usermod -aG docker "$USER"
    echo "Hinweis: Die neue Docker-Gruppenzugehörigkeit ist gegebenenfalls erst nach einer erneuten Anmeldung aktiv."
  else
    echo "Hinweis: Docker fehlt; die Websuche bleibt vorerst deaktiviert."
  fi
fi

mkdir -p "$TARGET" "$DATA_ROOT" "$BIN_DIR"
tar --exclude=.git --exclude=.venv --exclude=.codex-local --exclude=build --exclude=out --exclude=dist --exclude=.mypy_cache --exclude=.ruff_cache --exclude=__pycache__ -cf - -C "$SCRIPT_ROOT" . | tar -xf - -C "$TARGET"
run python3 -m venv "$TARGET/.venv"
run "$TARGET/.venv/bin/pip" install --disable-pip-version-check -r "$TARGET/requirements-local-codex.txt"

if [[ "$SKIP_SETUP" == 0 && -z "${WSL_DISTRO_NAME:-}" ]]; then
  base="https://github.com/$REPOSITORY/releases/download/v$VERSION"
  monitor_archive="$(mktemp)"
  checksums="$(mktemp)"
  curl -fL "$base/localcodex-monitor-linux-x64-v$VERSION.tar.gz" -o "$monitor_archive"
  curl -fL "$base/SHA256SUMS" -o "$checksums"
  expected="$(awk '/localcodex-monitor-linux-x64-v'"$VERSION"'\.tar\.gz$/ {print $1}' "$checksums")"
  [[ -n "$expected" && "$(sha256sum "$monitor_archive" | awk '{print $1}')" == "$expected" ]] || { echo "Monitor-Checksum ungültig." >&2; exit 1; }
  mkdir -p "$TARGET/prebuilt-monitor"
  tar -xzf "$monitor_archive" -C "$TARGET/prebuilt-monitor"
  export LOCAL_CODEX_MONITOR_EXE="$TARGET/prebuilt-monitor/localcodex-monitor"
fi

if [[ "$SKIP_SETUP" == 0 && "$SKIP_MODELS" == 0 ]]; then
  export LOCAL_CODEX_HOME="$DATA_ROOT"
  model_list="$(cd "$TARGET" && .venv/bin/python -c 'from local_codex.settings import SOURCE_MODELS; print("\n".join(dict.fromkeys(SOURCE_MODELS.values())))')"
  printf 'Ollama source models / Quellmodelle:\n%s\n' "$model_list"
  if confirm "Download these models / Diese Modelle herunterladen?"; then
    while IFS= read -r source_model; do
      run ollama pull "$source_model"
    done <<< "$model_list"
  fi
fi

if [[ "$SKIP_SETUP" == 0 ]]; then
  export LOCAL_CODEX_HOME="$DATA_ROOT"
  run "$TARGET/.venv/bin/python" "$TARGET/start_codex.py" setup
fi

if [[ "$SKIP_SEARCH" == 0 ]] && ! command -v docker >/dev/null; then
  echo "Hinweis: Docker fehlt; die Websuche kann später mit 'codex-local --search-doctor' eingerichtet werden."
fi

if [[ -L "$INSTALL_ROOT/current" ]]; then
  previous="$(readlink "$INSTALL_ROOT/current")"
  ln -sfn "$previous" "$INSTALL_ROOT/previous"
fi
ln -sfn "$TARGET" "$INSTALL_ROOT/current.new"
mv -Tf "$INSTALL_ROOT/current.new" "$INSTALL_ROOT/current"

wrapper="$BIN_DIR/codex-local"
{
  echo '#!/usr/bin/env bash'
  echo "export LOCAL_CODEX_INSTALL_ROOT=\"$INSTALL_ROOT\""
  echo "export LOCAL_CODEX_HOME=\"$DATA_ROOT\""
  echo 'exec "$LOCAL_CODEX_INSTALL_ROOT/current/.venv/bin/python" "$LOCAL_CODEX_INSTALL_ROOT/current/start_codex.py" "$@"'
} > "$wrapper"
chmod 0755 "$wrapper"

echo "LocalCodex $VERSION ist installiert. Starte mit: $wrapper"
