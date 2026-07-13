# Isolation image for pipeline agents (built as bhw-agent:latest).
#
# The Codex app-server / Claude CLI binaries are NOT installed here — they are
# bind-mounted from the host venv at run time (so their protocol always matches
# the installed Python SDK). This image only needs the security toolchain plus
# the runtimes those bundled CLIs depend on (node for the Claude bundle).
#
# The container is launched read-only with only /work, /agent-home and a tmpfs
# writable; see bug_hunter/core/sandbox.py.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Base runtimes + apt-based security tools (mirrors setup/ubuntu-24-04-setup.sh).
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq unzip \
      python3 python3-pip \
      nodejs npm \
      nmap masscan gobuster hydra nikto dirb sslscan whatweb sqlmap wapiti \
    && rm -rf /var/lib/apt/lists/*

# Go-based ProjectDiscovery suite, built into /usr/local/bin.
RUN apt-get update && apt-get install -y --no-install-recommends golang-go \
    && GOBIN=/usr/local/bin go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest \
    && GOBIN=/usr/local/bin go install github.com/projectdiscovery/httpx/cmd/httpx@latest \
    && GOBIN=/usr/local/bin go install github.com/projectdiscovery/katana/cmd/katana@latest \
    && GOBIN=/usr/local/bin go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest \
    && GOBIN=/usr/local/bin go install github.com/lc/gau/v2/cmd/gau@latest \
    && GOBIN=/usr/local/bin go install github.com/ffuf/ffuf/v2@latest \
    && GOBIN=/usr/local/bin go install github.com/hahwul/dalfox/v2@latest \
    && apt-get purge -y golang-go && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /root/go

# The bundled codex/claude binaries are mounted at /opt/{codex,claude} at run
# time; WORKDIR/HOME are set to /work by the launcher.
WORKDIR /work
