#!/usr/bin/env bash
#
# Idempotent setup for the Bug Hunting Workflow on Ubuntu 24.04.
# Installs the system toolchain, backend Python deps, the built frontend,
# and the security tooling the pipeline uses. Safe to re-run: every step
# checks its own state before acting, and per-tool failures warn instead of
# aborting the whole script.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Use sudo only when we're not already root.
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

# Keep apt fully non-interactive (no whiptail prompts, no service-restart menus).
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export NEEDRESTART_SUSPEND=1

log() { echo "==> $*"; }
warn() { echo "!!  $*" >&2; }

# Install an apt package only if it isn't already present.
apt_install() {
  local pkg
  for pkg in "$@"; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
      log "Installing apt package: $pkg"
      $SUDO apt install -y "$pkg" || warn "failed to install $pkg (continuing)"
    fi
  done
}

# ============================================================================
# 1. System base packages
# ============================================================================

if ! grep -rq "neovim-ppa/unstable" /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
  log "Adding neovim PPA"
  $SUDO add-apt-repository -y ppa:neovim-ppa/unstable
  $SUDO apt update
fi

apt_install neovim ca-certificates curl git unzip jq build-essential \
            golang-go python3 python3-venv python3-pip

# ============================================================================
# 2. Node.js + npm (for building the frontend)
# ============================================================================
# Ubuntu 24.04 ships Node 18.19, which satisfies Vite's "Node 18+" requirement.
# The distro `nodejs` package does not include npm, so install both.

apt_install nodejs npm

# ============================================================================
# 3. Claude + Codex CLIs
# ============================================================================

if ! command -v claude >/dev/null 2>&1; then
  log "Installing Claude CLI"
  curl -fsSL https://claude.ai/install.sh | bash
fi

if ! command -v codex >/dev/null 2>&1; then
  log "Installing Codex CLI (used for codex login; the openai-codex SDK ships its own runtime)"
  curl -fsSL https://chatgpt.com/codex/install.sh | sh
fi

# ============================================================================
# 4. Docker
# ============================================================================

if ! [ -f /etc/apt/keyrings/docker.asc ]; then
  log "Adding Docker GPG key"
  $SUDO install -m 0755 -d /etc/apt/keyrings
  $SUDO curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  $SUDO chmod a+r /etc/apt/keyrings/docker.asc
fi

if ! [ -f /etc/apt/sources.list.d/docker.sources ]; then
  log "Adding Docker apt repository"
  $SUDO tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  $SUDO apt update
fi

apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# ============================================================================
# 5. Backend — Python virtualenv + requirements
# ============================================================================
# Ubuntu 24.04 marks the system Python as externally-managed (PEP 668), so a
# venv is required — a system-wide `pip install` would be refused.

VENV_DIR="$PROJECT_ROOT/.venv"
if [ ! -d "$VENV_DIR" ]; then
  log "Creating Python virtualenv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
log "Installing backend Python requirements"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$PROJECT_ROOT/requirements.txt"

# ============================================================================
# 6. Frontend — install deps and build the production bundle
# ============================================================================

if [ -f "$PROJECT_ROOT/frontend/package.json" ]; then
  log "Installing frontend dependencies"
  ( cd "$PROJECT_ROOT/frontend" && npm install )
  log "Building frontend bundle (frontend/dist)"
  ( cd "$PROJECT_ROOT/frontend" && npm run build )
fi

# ============================================================================
# 7. Security tooling used by the pipeline
# ============================================================================
# These mirror bug_hunter/utils/tools.py. The app also auto-installs any
# missing tool at runtime (config: auto_install_tools), so this section is a
# best-effort head start — individual failures are non-fatal.

log "Installing apt-based security tools"
apt_install nmap masscan gobuster hydra nikto dirb sslscan whatweb sqlmap wapiti

# Go-based tools → install into /usr/local/bin so they're on PATH for everyone.
GO_BIN_DIR="/usr/local/bin"
install_go_tool() {
  local name="$1" pkg="$2"
  if command -v "$name" >/dev/null 2>&1; then
    return 0
  fi
  if ! command -v go >/dev/null 2>&1; then
    warn "go not available; skipping $name"
    return 0
  fi
  log "Installing $name (go install)"
  $SUDO env GOBIN="$GO_BIN_DIR" go install -v "$pkg" || warn "failed to install $name (the app can auto-install it at runtime)"
}

install_go_tool subfinder "github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest"
install_go_tool httpx     "github.com/projectdiscovery/httpx/cmd/httpx@latest"
install_go_tool katana    "github.com/projectdiscovery/katana/cmd/katana@latest"
install_go_tool nuclei    "github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest"
install_go_tool gau       "github.com/lc/gau/v2/cmd/gau@latest"
install_go_tool ffuf      "github.com/ffuf/ffuf/v2@latest"
install_go_tool dalfox    "github.com/hahwul/dalfox/v2@latest"

# ============================================================================
# 8. Agent isolation image (optional: config sandbox.enabled)
# ============================================================================
# Build the bhw-agent image used to run each hunting agent in its own container.
# Skipped if it already exists; rebuild with `docker build` manually to refresh.

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  if ! $SUDO docker image inspect bhw-agent:latest >/dev/null 2>&1; then
    log "Building bhw-agent Docker image (agent isolation)"
    $SUDO docker build -t bhw-agent:latest -f "$PROJECT_ROOT/docker/agent.Dockerfile" "$PROJECT_ROOT/docker" \
      || warn "bhw-agent image build failed (agent isolation will be unavailable until built)"
  fi
else
  warn "Docker daemon not available; skipping bhw-agent image build (needed only when sandbox.enabled=true)"
fi

# ============================================================================
# Done
# ============================================================================

log "Setup complete"
echo
echo "Next steps:"
echo "  1. Authenticate the agents:  claude   (login)   and   codex login"
echo "  2. Start the server:         $VENV_DIR/bin/python -m bug_hunter.main"
echo "     (serves the built frontend on http://0.0.0.0:80 by default)"
