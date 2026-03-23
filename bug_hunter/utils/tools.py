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
}

SOURCE_CODE_TOOLS: dict[str, dict] = {}

BLACK_BOX_TOOLS = {
    "subfinder": {
        "required": False,
        "install": "go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest",
        "description": "Subdomain discovery",
    },
    "httpx": {
        "required": False,
        "install": "go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest",
        "description": "HTTP probing",
    },
    "nmap": {
        "required": False,
        "install": "apt-get install -y nmap",
        "description": "Port scanner",
    },
    "ffuf": {
        "required": False,
        "install": "go install github.com/ffuf/ffuf/v2@latest",
        "description": "Web fuzzer",
    },
    "sqlmap": {
        "required": False,
        "install": "pip3 install sqlmap",
        "description": "SQL injection testing",
    },
}

OPTIONAL_TOOLS = {
    "codex": {"required": False, "install": None, "description": "Codex CLI"},
    "amass": {
        "required": False,
        "install": "go install -v github.com/owasp-amass/amass/v4/...@master",
        "description": "Subdomain enumeration",
    },
}


@dataclass
class ToolCheckResult:
    name: str
    available: bool
    path: str = ""
    installed: bool = False
    install_error: str = ""
    description: str = ""


async def check_tool(name: str) -> ToolCheckResult:
    """Check if a tool is available on the system."""
    path = shutil.which(name)
    desc = ""
    for toolset in [COMMON_TOOLS, SOURCE_CODE_TOOLS, BLACK_BOX_TOOLS, OPTIONAL_TOOLS]:
        if name in toolset:
            desc = toolset[name].get("description", "")
            break
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
        if not result.available and auto_install and info.get("install"):
            result = await install_tool(name, info["install"])
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
            }
            for r in results
        ],
        "all_required_available": all(
            r.available for r in results
            if any(
                r.name in ts and ts[r.name].get("required", False)
                for ts in [COMMON_TOOLS, SOURCE_CODE_TOOLS, BLACK_BOX_TOOLS]
            )
        ),
    }
