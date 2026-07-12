#!/usr/bin/env bash
#
# Idempotent setup for the Bug Hunting Workflow on Ubuntu 24.04.
# Safe to re-run: every step checks its own state before acting.
#
set -euo pipefail

# Use sudo only when we're not already root.
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

# --- neovim + base packages ---------------------------------------------------

if ! grep -rq "neovim-ppa/unstable" /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
  echo "==> Adding neovim PPA"
  $SUDO add-apt-repository -y ppa:neovim-ppa/unstable
  $SUDO apt update
fi

for pkg in neovim ca-certificates curl; do
  if ! dpkg -s "$pkg" >/dev/null 2>&1; then
    echo "==> Installing $pkg"
    $SUDO apt install -y "$pkg"
  fi
done

# --- Claude CLI ---------------------------------------------------------------

if ! command -v claude >/dev/null 2>&1; then
  echo "==> Installing Claude CLI"
  curl -fsSL https://claude.ai/install.sh | bash
fi

# --- Codex CLI ----------------------------------------------------------------

if ! command -v codex >/dev/null 2>&1; then
  echo "==> Installing Codex CLI"
  curl -fsSL https://chatgpt.com/codex/install.sh | sh
fi

# --- Docker -------------------------------------------------------------------

if ! [ -f /etc/apt/keyrings/docker.asc ]; then
  echo "==> Adding Docker GPG key"
  $SUDO install -m 0755 -d /etc/apt/keyrings
  $SUDO curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  $SUDO chmod a+r /etc/apt/keyrings/docker.asc
fi

if ! [ -f /etc/apt/sources.list.d/docker.sources ]; then
  echo "==> Adding Docker apt repository"
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

for pkg in docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; do
  if ! dpkg -s "$pkg" >/dev/null 2>&1; then
    echo "==> Installing $pkg"
    $SUDO apt install -y "$pkg"
  fi
done

echo "==> Setup complete"
