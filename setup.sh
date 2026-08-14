#!/usr/bin/env bash
# Claude Trading — Setup & Update script
#
# First run  : installs all deps, builds UI, creates systemd service, starts app
# Re-run     : pulls latest git changes (if repo), reinstalls only what changed,
#              rebuilds UI only if source files are newer, then restarts service
#
# Usage (no chmod needed — the executable bit is tracked in git):
#   sudo ./setup.sh            # normal run
#   sudo ./setup.sh --force    # force full reinstall + UI rebuild
#   sudo ./setup.sh --help     # show usage

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
START_TS=$(date +%s)

# ── Colours ───────────────────────────────────────────────────────────────────
# Stay quiet when the output isn't a terminal (piped to a file, run from CI) or
# when NO_COLOR is set — escape codes in a log file help nobody.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
    GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
    CYAN='\033[0;36m';  BOLD='\033[1m';      DIM='\033[2m'
    NC='\033[0m'
    # Banner gradient, matching the dashboard's blue→violet logo. Falls back to
    # plain cyan on terminals without 24-bit colour.
    if [[ "${COLORTERM:-}" == *truecolor* || "${COLORTERM:-}" == *24bit* ]]; then
        G1='\033[38;2;96;165;250m'   # blue-400
        G2='\033[38;2;129;140;248m'  # indigo-400
        G3='\033[38;2;167;139;250m'  # violet-400
    else
        G1="$CYAN"; G2="$CYAN"; G3="$CYAN"
    fi
else
    GREEN=''; YELLOW=''; RED=''; CYAN=''; BOLD=''; DIM=''; NC=''
    G1=''; G2=''; G3=''
fi

info()    { echo -e "  ${GREEN}✓${NC}  $*"; }
warn()    { echo -e "  ${YELLOW}!${NC}  $*"; }
err()     { echo -e "  ${RED}✗${NC}  $*"; }
skip()    { echo -e "  ${DIM}·${NC}  $* ${DIM}(up to date)${NC}"; }

# Seconds since $1, formatted for humans — only worth printing when a step
# actually took a while.
elapsed() {
    local d=$(($(date +%s) - $1))
    if   [ "$d" -lt 2  ]; then echo ""
    elif [ "$d" -lt 60 ]; then echo "${DIM}(${d}s)${NC}"
    else                       echo "${DIM}($((d / 60))m $((d % 60))s)${NC}"
    fi
}

# Draws a box that always fits its message, however long it is.
#
# Width is counted in characters rather than with ${#msg}, which counts BYTES
# under a non-UTF-8 locale — an em-dash is three bytes, so the rule came out
# wider than the text it was meant to frame.
box() {
    local colour="$1"; shift
    local msg="$*" rule width
    width=$(printf '%s' "$msg" | LC_ALL=C.UTF-8 wc -m 2>/dev/null | tr -d '[:space:]')
    [[ "$width" =~ ^[0-9]+$ ]] || width=${#msg}
    printf -v rule '%*s' "$(( width + 2 ))" ''
    echo -e "${colour}╔${rule// /═}╗${NC}"
    echo -e "${colour}║ ${msg} ║${NC}"
    echo -e "${colour}╚${rule// /═}╝${NC}"
}

STEP=0
section() {
    STEP=$((STEP + 1))
    SECTION_TS=$(date +%s)
    printf "\n${DIM}[%d/%d]${NC} ${CYAN}${BOLD}%s${NC}\n" "$STEP" "$TOTAL_STEPS" "$*"
}

banner() {
    echo ""
    echo -e "${G1}${BOLD}    ╔═╗╦  ╔═╗╦ ╦╔╦╗╔═╗  ╔╦╗╦═╗╔═╗╔╦╗╦╔╗╔╔═╗${NC}"
    echo -e "${G2}${BOLD}    ║  ║  ╠═╣║ ║ ║║║╣    ║ ╠╦╝╠═╣ ║║║║║║║ ╦${NC}"
    echo -e "${G3}${BOLD}    ╚═╝╩═╝╩ ╩╚═╝═╩╝╚═╝   ╩ ╩╚═╩ ╩═╩╝╩╝╚╝╚═╝${NC}"
    echo -e "${GREEN}      ▁▂▃▅▄▆█▆▄▅▇█▇▅▃▄▆█▇▆█${NC}  ${DIM}algorithmic trading${NC}"
    echo ""
}

usage() {
    banner
    cat <<'USAGE'
  Usage:  sudo ./setup.sh [options]

  Options:
    --force      Reinstall every dependency and rebuild the UI, even when
                 nothing has changed. Use after a corrupted install.
    --no-color   Disable coloured output (NO_COLOR=1 does the same).
    --help       Show this message.

  With no options the script only does what is actually needed: it pulls new
  commits, reinstalls dependencies whose manifests changed, rebuilds the UI if
  its sources are newer than the last build, then restarts the service.
USAGE
    echo ""
    exit 0
}

# ── Arguments ─────────────────────────────────────────────────────────────────
FORCE=false
for arg in "$@"; do
    case "$arg" in
        --force)    FORCE=true ;;
        --no-color) GREEN=''; YELLOW=''; RED=''; CYAN=''; BOLD=''; DIM=''
                    NC=''; G1=''; G2=''; G3='' ;;
        -h|--help)  usage ;;
        *)          err "Unknown option: $arg"
                    echo "  Try ${BOLD}./setup.sh --help${NC}"
                    exit 1 ;;
    esac
done

CURRENT_USER=$(logname 2>/dev/null || whoami)

# Timestamp file: touched after a successful pip install
PY_STAMP="$SCRIPT_DIR/.venv/.install_stamp"

# ── Detect first run vs update ────────────────────────────────────────────────
FIRST_RUN=false
if [ ! -d "$SCRIPT_DIR/.venv" ] || [ ! -f "/etc/systemd/system/claude-trading.service" ]; then
    FIRST_RUN=true
fi

# System packages and the firewall rule only run on a first install.
TOTAL_STEPS=10
[ "$FIRST_RUN" = true ] && TOTAL_STEPS=12

banner
if [ "$FIRST_RUN" = true ]; then
    echo -e "  ${BOLD}First-time setup${NC} ${DIM}·${NC} installing everything from scratch"
else
    echo -e "  ${BOLD}Update & restart${NC} ${DIM}·${NC} only rebuilding what changed"
fi
echo -e "  ${DIM}${SCRIPT_DIR}${NC}"
[ "$FORCE" = true ] && warn "--force set: reinstalling everything regardless"

# ─────────────────────────────────────────────────────────────────────────────
# 1. System packages (first run only)
# ─────────────────────────────────────────────────────────────────────────────
if [ "$FIRST_RUN" = true ]; then
    section "System packages"
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        python3 python3-pip python3-venv \
        curl ca-certificates git
    info "System packages installed $(elapsed "$SECTION_TS")"
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
                # Don't let a failed pull kill the whole run. `set -e` used to
                # abort here at step 2 of 10 with a raw git error, leaving
                # nothing installed and nothing restarted — even though every
                # remaining step would have worked fine against local files.
                if git pull --ff-only; then
                    GIT_UPDATED=true
                    info "Repository updated to $(git rev-parse --short HEAD)"
                else
                    warn "Pull failed — continuing with the files already on disk"
                    DIRTY=$(git diff --name-only 2>/dev/null | head -5)
                    if [ -n "$DIRTY" ]; then
                        echo -e "  ${DIM}locally modified:${NC}"
                        echo "$DIRTY" | sed 's/^/    /'
                        echo -e "  ${DIM}to take the incoming version:${NC} git checkout -- <file> && git pull"
                    fi
                fi
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
    info "Python 3.11 installed $(elapsed "$SECTION_TS")"
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
    info "Node.js $(node -v) installed $(elapsed "$SECTION_TS")"
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
    echo -e "  ${DIM}installing — this can take a couple of minutes${NC}"
    pip install --quiet --upgrade pip setuptools wheel
    pip install --quiet -e ".[dev]"
    touch "$PY_STAMP"
    info "Python dependencies installed $(elapsed "$SECTION_TS")"
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
    echo -e "  ${DIM}installing — this can take a couple of minutes${NC}"
    rm -rf node_modules          # remove before reinstall to clear any bad perms
    npm install --silent
    info "Node dependencies installed $(elapsed "$SECTION_TS")"
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
    info "UI built → ui/dist/ $(elapsed "$SECTION_TS")"
else
    skip "UI (no source changes detected)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 9. Ensure logs directory exists
# ─────────────────────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"
mkdir -p logs
mkdir -p profiles   # gitignored store for switchable trading profiles (keys live here)
# Fix ownership — setup runs as root but the service runs as $CURRENT_USER
chown -R "$CURRENT_USER":"$CURRENT_USER" "$SCRIPT_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# 10. .env check
# ─────────────────────────────────────────────────────────────────────────────
section "Environment file"
NEEDS_KEYS=false
if [ ! -f ".env" ]; then
    cp .env.example .env
    warn ".env created from .env.example — add your Alpaca API keys before starting the bot"
    NEEDS_KEYS=true
else
    # Warn if keys are still the placeholder values
    if grep -q "your_paper_api_key_here" .env 2>/dev/null; then
        warn ".env has placeholder keys — edit .env before starting the bot!"
        NEEDS_KEYS=true
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
TOTAL=$(( $(date +%s) - START_TS ))

echo ""
if [ "$SVC_OK" = true ]; then
    if [ "$FIRST_RUN" = true ]; then
        box "${GREEN}${BOLD}" "Setup complete — application is live"
    else
        box "${GREEN}${BOLD}" "Update applied — application restarted"
    fi
else
    box "${RED}${BOLD}" "Setup finished but the service did not start cleanly"
fi

echo ""
echo -e "  ${DIM}Dashboard${NC}   ${GREEN}${BOLD}http://${SERVER_IP}:8000${NC}"
echo -e "  ${DIM}Finished${NC}    in $((TOTAL / 60))m $((TOTAL % 60))s"

# The one thing that will stop a fresh install from trading — say it last, where
# it won't scroll away.
if [ "$NEEDS_KEYS" = true ]; then
    echo ""
    echo -e "  ${YELLOW}${BOLD}Next step${NC}   add your Alpaca keys, then restart:"
    echo -e "    ${BOLD}nano .env${NC}"
    echo -e "    ${BOLD}sudo systemctl restart claude-trading${NC}"
fi

echo ""
echo -e "  ${DIM}Useful commands${NC}"
echo "    sudo systemctl status claude-trading   → service status"
echo "    journalctl -u claude-trading -f        → live logs"
echo "    sudo systemctl stop claude-trading     → stop the service"
echo "    sudo ./setup.sh --force                → force full rebuild"
echo ""
