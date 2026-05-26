#!/usr/bin/env bash
# Claude Trading — Setup & Update script
#
# First run  : installs all deps, builds UI, creates systemd service, starts app
# Re-run     : pulls latest git changes (if repo), reinstalls only what changed,
#              rebuilds UI only if source files are newer, then restarts service
#
# Usage:
#   chmod +x setup.sh
#   sudo ./setup.sh            # normal run
#   sudo ./setup.sh --force    # force full reinstall + UI rebuild

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "  ${GREEN}✓${NC}  $*"; }
warn()    { echo -e "  ${YELLOW}!${NC}  $*"; }
err()     { echo -e "  ${RED}✗${NC}  $*"; }
section() { echo -e "\n${CYAN}${BOLD}── $* ──${NC}"; }
skip()    { echo -e "  ${NC}·${NC}  $* ${CYAN}(up to date)${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORCE=false
[[ "${1:-}" == "--force" ]] && FORCE=true

# Timestamp file: touched after a successful pip install
PY_STAMP="$SCRIPT_DIR/.venv/.install_stamp"

# ── Detect first run vs update ────────────────────────────────────────────────
FIRST_RUN=false
if [ ! -d "$SCRIPT_DIR/.venv" ] || [ ! -f "/etc/systemd/system/claude-trading.service" ]; then
    FIRST_RUN=true
fi

if [ "$FIRST_RUN" = true ]; then
    echo -e "\n${BOLD}Claude Trading — First-time Setup${NC}"
else
    echo -e "\n${BOLD}Claude Trading — Update & Restart${NC}"
fi
[ "$FORCE" = true ] && warn "--force flag set: full reinstall will run"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. System packages (first run only)
# ─────────────────────────────────────────────────────────────────────────────
if [ "$FIRST_RUN" = true ]; then
    section "System packages"
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        python3 python3-pip python3-venv \
        curl ca-certificates git
    info "System packages installed"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2. Git pull (if this is a git repository)
# ─────────────────────────────────────────────────────────────────────────────
section "Checking for updates"
cd "$SCRIPT_DIR"

GIT_UPDATED=false
if [ -d ".git" ]; then
    # Make sure we have a remote configured
    if git remote get-url origin &>/dev/null; then
        info "Git repository found — fetching..."
        if git fetch origin 2>/dev/null; then
            LOCAL=$(git rev-parse HEAD 2>/dev/null || echo "none")
            REMOTE=$(git rev-parse "@{u}" 2>/dev/null || echo "none")

            if [ "$LOCAL" != "$REMOTE" ] && [ "$REMOTE" != "none" ]; then
                COMMITS=$(git rev-list --count HEAD..@{u} 2>/dev/null || echo "?")
                info "Pulling $COMMITS new commit(s)..."
                git pull --ff-only
                GIT_UPDATED=true
                info "Repository updated to $(git rev-parse --short HEAD)"
            else
                skip "Repository already at latest commit"
            fi
        else
            warn "Could not reach remote — continuing with local files"
        fi
    else
        warn "No git remote configured — skipping pull (files updated via WinSCP)"
    fi
else
    warn "Not a git repo — skipping pull (files updated via WinSCP)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. Python version
# ─────────────────────────────────────────────────────────────────────────────
section "Python"
PYTHON=$(command -v python3.11 2>/dev/null \
    || command -v python3.12 2>/dev/null \
    || command -v python3 2>/dev/null)

if "$PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
    PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    info "Python $PY_VER"
else
    warn "Python 3.11+ required — installing from deadsnakes PPA..."
    sudo apt-get install -y -qq software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3.11 python3.11-venv
    PYTHON=python3.11
    info "Python 3.11 installed"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4. Node.js 20 (first run or missing)
# ─────────────────────────────────────────────────────────────────────────────
section "Node.js"
if command -v node &>/dev/null \
   && node -e "process.exit(parseInt(process.version.slice(1)) >= 18 ? 0 : 1)" 2>/dev/null; then
    skip "Node.js $(node -v) already installed"
else
    warn "Installing Node.js 20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/nodesource_setup.sh
    sudo bash /tmp/nodesource_setup.sh
    sudo apt-get install -y -qq nodejs
    info "Node.js $(node -v) installed"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5. Python virtual environment
# ─────────────────────────────────────────────────────────────────────────────
section "Python virtual environment"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    "$PYTHON" -m venv .venv
    info "Virtual environment created"
else
    skip "Virtual environment exists"
fi
source .venv/bin/activate

# ─────────────────────────────────────────────────────────────────────────────
# 6. Python dependencies (only if pyproject.toml changed or forced)
# ─────────────────────────────────────────────────────────────────────────────
section "Python dependencies"

PY_NEEDS_INSTALL=false
if   [ "$FORCE" = true ];                          then PY_NEEDS_INSTALL=true
elif [ ! -f "$PY_STAMP" ];                         then PY_NEEDS_INSTALL=true
elif [ "pyproject.toml" -nt "$PY_STAMP" ];         then PY_NEEDS_INSTALL=true; info "pyproject.toml changed"
elif [ "$GIT_UPDATED" = true ];                    then PY_NEEDS_INSTALL=true
fi

if [ "$PY_NEEDS_INSTALL" = true ]; then
    pip install --quiet --upgrade pip setuptools wheel
    pip install --quiet -e ".[dev]"
    touch "$PY_STAMP"
    info "Python dependencies installed"
else
    skip "Python dependencies"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 7. Node dependencies (only if package.json changed)
# ─────────────────────────────────────────────────────────────────────────────
section "Node dependencies"
cd "$SCRIPT_DIR/ui"

NODE_NEEDS_INSTALL=false
if   [ "$FORCE" = true ];                                      then NODE_NEEDS_INSTALL=true
elif [ ! -d "node_modules" ];                                  then NODE_NEEDS_INSTALL=true
elif [ "package.json" -nt "node_modules/.package-lock.json" ]; then NODE_NEEDS_INSTALL=true; info "package.json changed"
elif [ "$GIT_UPDATED" = true ];                                then NODE_NEEDS_INSTALL=true
# WinSCP strips execute bits — detect by checking tsc is actually runnable
elif [ ! -x "node_modules/.bin/tsc" ];                         then NODE_NEEDS_INSTALL=true; warn "node_modules missing execute bits (WinSCP copy) — reinstalling"
fi

if [ "$NODE_NEEDS_INSTALL" = true ]; then
    rm -rf node_modules          # remove before reinstall to clear any bad perms
    npm install --silent
    info "Node dependencies installed"
else
    skip "Node dependencies"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 8. UI build (only if source files are newer than the built dist)
# ─────────────────────────────────────────────────────────────────────────────
section "UI build"
cd "$SCRIPT_DIR/ui"

UI_NEEDS_BUILD=false
if [ "$FORCE" = true ]; then
    UI_NEEDS_BUILD=true
elif [ ! -d "dist" ] || [ ! -f "dist/index.html" ]; then
    UI_NEEDS_BUILD=true
elif find src -newer dist/index.html \
        \( -name "*.tsx" -o -name "*.ts" -o -name "*.css" -o -name "*.json" \) \
        2>/dev/null | grep -q .; then
    UI_NEEDS_BUILD=true
    info "Source files changed"
elif [ "$GIT_UPDATED" = true ]; then
    UI_NEEDS_BUILD=true
fi

if [ "$UI_NEEDS_BUILD" = true ]; then
    npm run build
    info "UI built → ui/dist/"
else
    skip "UI (no source changes detected)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 9. Ensure logs directory exists
# ─────────────────────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"
mkdir -p logs

# ─────────────────────────────────────────────────────────────────────────────
# 10. .env check
# ─────────────────────────────────────────────────────────────────────────────
section "Environment file"
if [ ! -f ".env" ]; then
    cp .env.example .env
    warn ".env created from .env.example — add your Alpaca API keys before starting the bot"
else
    # Warn if keys are still the placeholder values
    if grep -q "your_paper_api_key_here" .env 2>/dev/null; then
        warn ".env has placeholder keys — edit .env before starting the bot!"
    else
        info ".env configured"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 11. systemd service (always re-write so paths stay current)
# ─────────────────────────────────────────────────────────────────────────────
section "systemd service"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
SERVICE_FILE="/etc/systemd/system/claude-trading.service"
CURRENT_USER=$(logname 2>/dev/null || whoami)

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Claude Trading Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_PYTHON -m uvicorn api.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=10
TimeoutStopSec=15
StandardOutput=journal
StandardError=journal
Environment=PATH=$SCRIPT_DIR/.venv/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable claude-trading --quiet
info "Service configured and enabled (auto-starts on boot)"

# ─────────────────────────────────────────────────────────────────────────────
# 12. Firewall (first run only)
# ─────────────────────────────────────────────────────────────────────────────
if [ "$FIRST_RUN" = true ]; then
    section "Firewall"
    if command -v ufw &>/dev/null; then
        sudo ufw allow 8000/tcp
        info "Port 8000 open in ufw"
    else
        warn "ufw not found — make sure port 8000 is accessible on your network"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 13. Start / restart the service
# ─────────────────────────────────────────────────────────────────────────────
section "Launching application"
if sudo systemctl is-active --quiet claude-trading 2>/dev/null; then
    sudo systemctl restart claude-trading
    ACTION="Restarted"
else
    sudo systemctl start claude-trading
    ACTION="Started"
fi

# Give systemd 3 seconds to confirm the process came up
sleep 3

if sudo systemctl is-active --quiet claude-trading 2>/dev/null; then
    info "$ACTION successfully — service is ${GREEN}running${NC}"
    SVC_OK=true
else
    err "Service failed to start"
    echo ""
    echo "  Last journal lines:"
    sudo journalctl -u claude-trading -n 20 --no-pager | sed 's/^/    /'
    SVC_OK=false
fi

# ─────────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────────
SERVER_IP=$(hostname -I | awk '{print $1}')

echo ""
if [ "$SVC_OK" = true ]; then
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
    if [ "$FIRST_RUN" = true ]; then
    echo -e "${GREEN}${BOLD}║  Setup complete — application is live!               ║${NC}"
    else
    echo -e "${GREEN}${BOLD}║  Update applied — application restarted!             ║${NC}"
    fi
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
else
    echo -e "${RED}${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}${BOLD}║  Setup finished but service did not start cleanly    ║${NC}"
    echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
fi
echo ""
echo -e "  Dashboard:      ${GREEN}${BOLD}http://${SERVER_IP}:8000${NC}"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status claude-trading   → service status"
echo "    journalctl -u claude-trading -f        → live logs"
echo "    sudo systemctl stop claude-trading     → stop the service"
echo "    sudo ./setup.sh --force                → force full rebuild"
echo ""
