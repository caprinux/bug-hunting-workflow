"""Tool dependency checker and auto-installer."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass

logger = logging.getLogger(__name__)

COMMON_TOOLS = {
    "claude": {"required": True, "install": None, "description": "Claude Code CLI"},
    "git": {"required": True, "install": "apt-get install -y git", "description": "Git VCS"},
    "python3": {"required": True, "install": "apt-get install -y python3", "description": "Python 3"},
    "pip3": {"required": True, "install": "apt-get install -y python3-pip", "description": "Python pip"},
    "curl": {"required": True, "install": "apt-get install -y curl", "description": "HTTP client"},
    "go": {"required": True, "install": "apt-get install -y golang-go", "description": "Go runtime (for installing security tools)"},
    "unzip": {"required": True, "install": "apt-get install -y unzip", "description": "Archive extraction"},
    "jq": {"required": True, "install": "apt-get install -y jq", "description": "JSON processor"},
}

SOURCE_CODE_TOOLS: dict[str, dict] = {
    "jadx": {
        "required": False,
        "install": "bash -c 'JADX_VER=$(curl -sL https://api.github.com/repos/skylot/jadx/releases/latest | python3 -c \"import sys,json;print(json.load(sys.stdin)[\\\"tag_name\\\"].lstrip(\\\"v\\\"))\") && curl -sL https://github.com/skylot/jadx/releases/download/v${JADX_VER}/jadx-${JADX_VER}.zip -o /tmp/jadx.zip && unzip -qo /tmp/jadx.zip -d /opt/jadx-${JADX_VER} && ln -sf /opt/jadx-${JADX_VER}/bin/jadx /usr/local/bin/jadx && rm /tmp/jadx.zip'",
        "description": "Android APK/DEX decompiler",
    },
}

BLACK_BOX_TOOLS = {
    # Recon & Discovery
    "subfinder": {
        "required": True,
        "install": "go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
        "description": "Subdomain discovery",
    },
    "httpx": {
        "required": True,
        "install": "go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest",
        "description": "HTTP probing and tech fingerprinting",
        "verify": "httpx -version",
    },
    "nmap": {
        "required": True,
        "install": "apt-get install -y nmap",
        "description": "Port scanning and service detection",
    },
    "masscan": {
        "required": True,
        "install": "apt-get install -y masscan",
        "description": "Fast port scanning",
    },
    "katana": {
        "required": True,
        "install": "go install github.com/projectdiscovery/katana/cmd/katana@latest",
        "description": "Web crawling and endpoint discovery",
    },
    "gau": {
        "required": True,
        "install": "go install github.com/lc/gau/v2/cmd/gau@latest",
        "description": "Fetch known URLs from Wayback Machine and commoncrawl",
    },
    "nuclei": {
        "required": True,
        "install": "go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest",
        "description": "Template-based vulnerability scanner",
    },
    # Web Fuzzing & Directory Brute
    "ffuf": {
        "required": True,
        "install": "go install github.com/ffuf/ffuf/v2@latest",
        "description": "Web fuzzer for directories, parameters, and vhosts",
    },
    "gobuster": {
        "required": True,
        "install": "apt-get install -y gobuster",
        "description": "Directory and DNS brute-forcing",
    },
    "feroxbuster": {
        "required": True,
        "install": "apt-get install -y feroxbuster",
        "description": "Recursive content discovery",
    },
    "dirb": {
        "required": True,
        "install": "apt-get install -y dirb",
        "description": "Directory brute-forcing with wordlists",
    },
    # Vulnerability Scanning
    "sqlmap": {
        "required": True,
        "install": "apt-get install -y sqlmap",
        "description": "SQL injection detection and exploitation",
    },
    "nikto": {
        "required": True,
        "install": "apt-get install -y nikto",
        "description": "Web server vulnerability scanner",
    },
    "whatweb": {
        "required": True,
        "install": "apt-get install -y whatweb",
        "description": "Web technology fingerprinting",
    },
    "wapiti": {
        "required": True,
        "install": "apt-get install -y wapiti",
        "description": "Web application vulnerability scanner",
    },
    "sslscan": {
        "required": True,
        "install": "apt-get install -y sslscan",
        "description": "SSL/TLS configuration analysis",
    },
    "dalfox": {
        "required": True,
        "install": "go install github.com/hahwul/dalfox/v2@latest",
        "description": "XSS vulnerability scanner",
    },
    # Auth & Brute Force
    "hydra": {
        "required": True,
        "install": "apt-get install -y hydra",
        "description": "Network login brute-forcer",
    },
    # Mobile
    "jadx": {
        "required": True,
        "install": "bash -c 'JADX_VER=$(curl -sL https://api.github.com/repos/skylot/jadx/releases/latest | python3 -c \"import sys,json;print(json.load(sys.stdin)[\\\"tag_name\\\"].lstrip(\\\"v\\\"))\") && curl -sL https://github.com/skylot/jadx/releases/download/v${JADX_VER}/jadx-${JADX_VER}.zip -o /tmp/jadx.zip && unzip -qo /tmp/jadx.zip -d /opt/jadx-${JADX_VER} && ln -sf /opt/jadx-${JADX_VER}/bin/jadx /usr/local/bin/jadx && rm /tmp/jadx.zip'",
        "description": "Android APK/DEX decompiler",
    },
}

OPTIONAL_TOOLS = {
    "codex": {"required": False, "install": None, "description": "Codex CLI"},
}


@dataclass
class ToolCheckResult:
    name: str
    available: bool
    path: str = ""
    installed: bool = False
    install_error: str = ""
    description: str = ""
    required: bool = False


def _ensure_go_bin_in_path():
    """Add Go bin directories to PATH if not already present."""
    import os as _os
    go_path = _os.environ.get("GOPATH", _os.path.expanduser("~/go"))
    go_bin = _os.environ.get("GOBIN", _os.path.join(go_path, "bin"))
    path_entries = _os.environ.get("PATH", "").split(_os.pathsep)
    if go_bin not in path_entries:
        _os.environ["PATH"] = go_bin + _os.pathsep + _os.environ.get("PATH", "")


async def check_tool(name: str) -> ToolCheckResult:
    """Check if a tool is available on the system."""
    _ensure_go_bin_in_path()
    path = shutil.which(name)
    desc = ""
    verify_cmd = None
    for toolset in [COMMON_TOOLS, SOURCE_CODE_TOOLS, BLACK_BOX_TOOLS, OPTIONAL_TOOLS]:
        if name in toolset:
            desc = toolset[name].get("description", "")
            verify_cmd = toolset[name].get("verify")
            break

    # If found but has a verify command, run it to confirm it's the right binary
    if path and verify_cmd:
        try:
            proc = await asyncio.create_subprocess_shell(
                verify_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                path = None  # Wrong binary (e.g. Python httpx vs ProjectDiscovery httpx)
        except (asyncio.TimeoutError, Exception):
            path = None

    return ToolCheckResult(
        name=name,
        available=path is not None,
        path=path or "",
        description=desc,
    )


async def install_tool(name: str, install_cmd: str) -> ToolCheckResult:
    """Attempt to install a tool."""
    logger.info(f"Installing {name}: {install_cmd}")
    try:
        process = await asyncio.create_subprocess_shell(
            install_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
        if process.returncode == 0:
            result = await check_tool(name)
            result.installed = True
            logger.info(f"Successfully installed {name}")
            return result
        else:
            error = stderr.decode("utf-8", errors="replace")
            logger.error(f"Failed to install {name}: {error}")
            return ToolCheckResult(
                name=name, available=False, installed=False,
                install_error=error, description=name,
            )
    except asyncio.TimeoutError:
        return ToolCheckResult(
            name=name, available=False, installed=False,
            install_error="Installation timed out", description=name,
        )
    except Exception as e:
        return ToolCheckResult(
            name=name, available=False, installed=False,
            install_error=str(e), description=name,
        )


async def check_and_install_tools(engagement_type: str, auto_install: bool = True) -> list[ToolCheckResult]:
    """Check all required tools and optionally auto-install missing ones."""
    toolsets = dict(COMMON_TOOLS)
    if engagement_type == "source_code":
        toolsets.update(SOURCE_CODE_TOOLS)
    elif engagement_type == "black_box":
        toolsets.update(BLACK_BOX_TOOLS)
    toolsets.update(OPTIONAL_TOOLS)

    results = []
    for name, info in toolsets.items():
        result = await check_tool(name)
        result.required = info.get("required", False)
        if not result.available and auto_install and info.get("install"):
            result = await install_tool(name, info["install"])
            result.required = info.get("required", False)
        results.append(result)

    return results


def tools_report(results: list[ToolCheckResult]) -> dict:
    """Generate a structured report of tool check results."""
    return {
        "tools": [
            {
                "name": r.name,
                "available": r.available,
                "path": r.path,
                "installed": r.installed,
                "install_error": r.install_error,
                "description": r.description,
                "required": r.required,
            }
            for r in results
        ],
        "all_required_available": all(
            r.available for r in results if r.required
        ),
    }
