#!/usr/bin/env bash
# Acai installer — run with:
#   curl -sSL https://raw.githubusercontent.com/kiwi-lang/acai/main/install.sh | bash
#
# Installs to /opt/acai/ with:
#   .venv/      — Python virtual environment
#   workspace/  — conversations, agents, store, queue (git-tracked if configured)
#
# Safe to re-run: upgrades the package, preserves data.
#
# From a git checkout (install from source instead of PyPI):
#   curl -sSL https://raw.githubusercontent.com/kiwi-lang/acai/main/install.sh | bash -s -- --from-source
#   bash install.sh --from-source
#   # optional: ACAI_SOURCE_CLONE_URL=https://github.com/you/fork.git
#   ACAI_SOURCE_ROOT=/path/to/assai bash install.sh --from-source
#
set -euo pipefail

info()  { printf '\033[1;34m=> %s\033[0m\n' "$*" >&2; }
ok()    { printf '\033[1;32m✓  %s\033[0m\n' "$*" >&2; }
warn()  { printf '\033[1;33m!  %s\033[0m\n' "$*" >&2; }
fail()  { printf '\033[1;31m✗  %s\033[0m\n' "$*" >&2; exit 1; }

BASE="/opt/acai"
VENV="$BASE/.venv"
WORKSPACE="$BASE/workspace"
SERVICE_FILE="/etc/systemd/system/acai.service"
PORT="${ACAI_PORT:-5050}"
PYTHON_VERSION="3.12"
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"

INSTALL_FROM_SOURCE=false
SOURCE_ROOT_CLI=""

usage() {
    sed 's/^    //' <<'EOF'
    Usage: install.sh [options]

    Default: install / upgrade the acai-swarm package from PyPI into /opt/acai.

    Options:
      --from-source, -s   Install from a local git checkout: sync the tree to
                          match origin (fetch + reset --hard; no merge prompts),
                          then install. If requirements.txt exists in the repo,
                          dependencies are installed from it first (CUDA / vLLM
                          indexes), then the package with --no-deps.
      --source-root DIR   Use DIR as the package root (must contain pyproject.toml).
                          Overrides ACAI_SOURCE_ROOT. Implies --from-source.
      -h, --help          Show this help and exit.

    Environment:
      ACAI_FROM_SOURCE=1     Same as --from-source.
      ACAI_SOURCE_ROOT       Directory of the acai-swarm repo (with pyproject.toml).
      ACAI_SOURCE_CLONE_URL  Git URL to clone into /opt/acai/.source when that tree
                             is missing (default: https://github.com/kiwi-lang/acai.git).
      ACAI_UV_REQUIREMENTS=1 Try uv first for requirements.txt, then pip if resolution
                             fails. By default pip is used for requirements.txt because
                             the CUDA / vLLM pin set is often unsatisfiable under uv alone.

    Resolution order: --source-root, ACAI_SOURCE_ROOT, then /opt/acai/.source if it
    is already a valid checkout, then the directory of this script, then the current
    directory. If still unresolved and neither --source-root nor ACAI_SOURCE_ROOT
    was set, the script clones ACAI_SOURCE_CLONE_URL into /opt/acai/.source.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from-source|-s)
            INSTALL_FROM_SOURCE=true
            shift
            ;;
        --source-root=*)
            SOURCE_ROOT_CLI="${1#*=}"
            INSTALL_FROM_SOURCE=true
            shift
            ;;
        --source-root)
            [[ $# -ge 2 ]] || fail "--source-root requires a directory argument"
            SOURCE_ROOT_CLI="$2"
            INSTALL_FROM_SOURCE=true
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown option: $1 (try --help)"
            ;;
    esac
done

if [[ "${ACAI_FROM_SOURCE:-0}" == "1" ]] || [[ "${ACAI_FROM_SOURCE:-}" == "true" ]]; then
    INSTALL_FROM_SOURCE=true
fi

_pyproject_is_acai_swarm() {
    local root="$1"
    [[ -f "$root/pyproject.toml" ]] || return 1
    grep -qE '^name[[:space:]]*=[[:space:]]*"acai-swarm"' "$root/pyproject.toml" 2>/dev/null \
        || grep -qE "^name[[:space:]]*=[[:space:]]*'acai-swarm'" "$root/pyproject.toml" 2>/dev/null
}

_clone_default_source_if_needed() {
    local default_src="$BASE/.source"
    local url="${ACAI_SOURCE_CLONE_URL:-https://github.com/kiwi-lang/acai.git}"

    if [[ -d "$default_src/.git" ]] && _pyproject_is_acai_swarm "$default_src"; then
        SOURCE_ROOT="$(cd "$default_src" && pwd)"
        return 0
    fi
    if [[ -e "$default_src" ]]; then
        if [[ -d "$default_src" ]] && [[ -z "$(ls -A "$default_src" 2>/dev/null || true)" ]]; then
            rmdir "$default_src" 2>/dev/null || true
        elif [[ -d "$default_src" ]]; then
            fail "$default_src exists but is not a usable acai-swarm git checkout. Remove it or set ACAI_SOURCE_ROOT."
        else
            fail "$default_src exists and is not a directory."
        fi
    fi
    info "Cloning $url → $default_src ..."
    mkdir -p "$BASE"
    git clone -q --depth 1 "$url" "$default_src"
    _pyproject_is_acai_swarm "$default_src" || fail "Cloned repo at $default_src is not acai-swarm (wrong URL?)"
    SOURCE_ROOT="$(cd "$default_src" && pwd)"
}

resolve_source_root() {
    local candidate="" here=""
    if [[ -n "${SOURCE_ROOT_CLI}" ]]; then
        candidate="$(cd "$SOURCE_ROOT_CLI" && pwd)"
        _pyproject_is_acai_swarm "$candidate" || fail "Not an acai-swarm repo (missing or wrong pyproject.toml): $candidate"
        SOURCE_ROOT="$candidate"
        return
    fi
    if [[ -n "${ACAI_SOURCE_ROOT:-}" ]]; then
        candidate="$(cd "$ACAI_SOURCE_ROOT" && pwd)"
        _pyproject_is_acai_swarm "$candidate" || fail "Not an acai-swarm repo (missing or wrong pyproject.toml): $candidate"
        SOURCE_ROOT="$candidate"
        return
    fi
    local default_src="$BASE/.source"
    if [[ -d "$default_src/.git" ]] && _pyproject_is_acai_swarm "$default_src"; then
        SOURCE_ROOT="$(cd "$default_src" && pwd)"
        return
    fi
    if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
        here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        if _pyproject_is_acai_swarm "$here"; then
            SOURCE_ROOT="$here"
            return
        fi
    fi
    here="$(pwd)"
    if _pyproject_is_acai_swarm "$here"; then
        SOURCE_ROOT="$here"
        return
    fi
    if [[ -z "${SOURCE_ROOT_CLI}" ]] && [[ -z "${ACAI_SOURCE_ROOT:-}" ]]; then
        _clone_default_source_if_needed
        return
    fi
    fail "Cannot find acai-swarm source at ACAI_SOURCE_ROOT / --source-root."
}

# Machine-owned checkout under /opt: match remote exactly (no merge/rebase prompts;
# survives force-pushes and divergent local commits — local .source changes are discarded).
sync_git_source_to_remote() {
    local src="$1"
    git -C "$src" fetch origin --prune
    local upstream
    if upstream=$(git -C "$src" rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null); then
        git -C "$src" reset --hard "$upstream"
        return 0
    fi
    local cur
    cur=$(git -C "$src" rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)
    if [[ "$cur" != "HEAD" ]] && git -C "$src" show-ref -q --verify "refs/remotes/origin/$cur"; then
        git -C "$src" reset --hard "origin/$cur"
        return 0
    fi
    local def
    def=$(git -C "$src" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||') || true
    if [[ -n "$def" ]] && git -C "$src" show-ref -q --verify "refs/remotes/origin/$def"; then
        git -C "$src" checkout -B "$def" "origin/$def"
        git -C "$src" reset --hard "origin/$def"
        return 0
    fi
    for b in main master; do
        if git -C "$src" show-ref -q --verify "refs/remotes/origin/$b"; then
            git -C "$src" checkout -B "$b" "origin/$b"
            git -C "$src" reset --hard "origin/$b"
            return 0
        fi
    done
    fail "Could not sync $src with origin (no usable origin/main or origin/master)."
}

# requirements.txt carries --extra-index-url / --find-links. The torch + vLLM cu130
# stack is often unsatisfiable under uv's resolver (cuda-bindings vs nvidia-cutlass-dsl);
# default to pip. Set ACAI_UV_REQUIREMENTS=1 to try uv first (--index-strategy /
# --prerelease=allow), then pip on failure.
install_requirements_bundle() {
    local req="$1"
    local py="$VENV/bin/python"
    if [[ "${ACAI_UV_REQUIREMENTS:-0}" == "1" ]] || [[ "${ACAI_UV_REQUIREMENTS:-}" == "true" ]]; then
        info "Installing dependencies with uv (-r $(basename "$req"))…"
        if uv pip install --python "$py" --upgrade \
            --index-strategy unsafe-best-match \
            --prerelease=allow \
            -r "$req"; then
            return 0
        fi
        warn "uv could not resolve this CUDA stack; using pip…"
    else
        info "Installing dependencies with pip (-r $(basename "$req"))…"
    fi
    "$py" -m pip install -q --upgrade pip
    "$py" -m pip install --upgrade -r "$req"
}

# -- Pre-flight -------------------------------------------------------------

info "Installing Acai to $BASE"

if [ ! -d "$BASE" ]; then
    sudo mkdir -p "$BASE"
    sudo chown "$RUN_USER:$RUN_GROUP" "$BASE"
fi

mkdir -p "$WORKSPACE"

# -- Stop existing service before upgrade ------------------------------------

if sudo systemctl is-active --quiet acai.service 2>/dev/null; then
    info "Stopping existing service..."
    sudo systemctl stop acai.service
fi

# -- Install uv if missing --------------------------------------------------

if ! command -v uv &>/dev/null; then
    if [ -x "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    else
        info "Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi
ok "uv $(uv --version)"

# -- Create venv & install package -------------------------------------------

NEED_VENV=false
if [ ! -d "$VENV" ]; then
    NEED_VENV=true
elif ! "$VENV/bin/python" --version 2>/dev/null | grep -q "Python $PYTHON_VERSION"; then
    CURRENT=$("$VENV/bin/python" --version 2>/dev/null || echo "missing")
    warn "Existing venv has $CURRENT, need Python $PYTHON_VERSION — recreating"
    rm -rf "$VENV"
    NEED_VENV=true
fi

if $NEED_VENV; then
    info "Creating virtual environment (Python $PYTHON_VERSION)..."
    uv venv --seed --python "$PYTHON_VERSION" "$VENV"
fi

if $INSTALL_FROM_SOURCE; then
    SOURCE_ROOT=""
    resolve_source_root
    [[ -n "$SOURCE_ROOT" ]] || fail "Internal error: empty SOURCE_ROOT after resolve."
    if [[ ! -d "$SOURCE_ROOT/.git" ]]; then
        fail "Source tree is not a git clone (no .git): $SOURCE_ROOT"
    fi
    info "Syncing source tree to match origin in $SOURCE_ROOT (fetch + reset --hard; discards local divergence)..."
    sync_git_source_to_remote "$SOURCE_ROOT"
    if [[ -f "$SOURCE_ROOT/requirements.txt" ]]; then
        info "Installing Python dependencies from requirements.txt (indexes + pins)..."
        install_requirements_bundle "$SOURCE_ROOT/requirements.txt"
        info "Installing acai-swarm from source ($SOURCE_ROOT, --no-deps)..."
        uv pip install --python "$VENV/bin/python" --upgrade --no-deps "$SOURCE_ROOT"
    else
        info "Installing acai-swarm from source ($SOURCE_ROOT)..."
        uv pip install --python "$VENV/bin/python" --upgrade "$SOURCE_ROOT"
    fi
else
    info "Installing/upgrading acai-swarm from PyPI..."
    uv pip install --python "$VENV/bin/python" --upgrade acai-swarm
fi

ok "acai-swarm $($VENV/bin/python -c 'import acai; print(acai.__version__)')"

# -- Install systemd service -------------------------------------------------

sudo tee "$SERVICE_FILE" > /dev/null <<SVC
[Unit]
Description=Acai AI agent swarm server
After=network.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_GROUP
ExecStart=$VENV/bin/acai uber --host 0.0.0.0 --port $PORT --extern_llm 1
WorkingDirectory=$BASE
Restart=on-failure
RestartForceExitStatus=42
RestartSec=3
Environment=PATH=$VENV/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=$HOME
Environment=ACAI_WORKSPACE=$WORKSPACE
Environment=ACAI_PORT=$PORT

[Install]
WantedBy=multi-user.target
SVC

sudo systemctl daemon-reload
sudo systemctl enable acai.service
sudo systemctl restart acai.service

# -- Done --------------------------------------------------------------------

echo ""
ok "Acai is running at http://localhost:$PORT"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status acai        # check status"
echo "    sudo systemctl restart acai       # restart"
echo "    sudo journalctl -u acai -f        # view logs"
echo ""
