#!/usr/bin/env bash
# Acai installer — run with:
#   curl -sSL https://raw.githubusercontent.com/kiwi-lang/acai/master/install.sh | bash
#
# Installs to /opt/acai/ with:
#   .venv/      — Python virtual environment
#   workspace/  — conversations, agents, store, queue (git-tracked if configured)
#
# Safe to re-run: upgrades the package, preserves data.
set -euo pipefail

BASE="/opt/acai"
VENV="$BASE/.venv"
WORKSPACE="$BASE/workspace"
SERVICE_FILE="/etc/systemd/system/acai.service"
PORT="${ACAI_PORT:-5050}"
PYTHON_VERSION="3.12"
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"

info()  { printf '\033[1;34m=> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m✓  %s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m!  %s\033[0m\n' "$*"; }
fail()  { printf '\033[1;31m✗  %s\033[0m\n' "$*"; exit 1; }

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

info "Installing/upgrading acai-swarm..."
uv pip install --python "$VENV/bin/python" --upgrade acai-swarm

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
