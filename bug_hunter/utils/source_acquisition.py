"""Source code acquisition — clone repos or validate local paths."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AcquisitionResult:
    success: bool
    local_path: str = ""
    error: str = ""
    repo_url: str = ""
    branch: str = ""
    commit: str = ""


def is_git_url(url: str) -> bool:
    """Check if a string looks like a git repo URL."""
    patterns = [
        r"^https?://",
        r"^git@",
        r"^ssh://",
        r"\.git$",
        r"github\.com/",
        r"gitlab\.com/",
        r"bitbucket\.org/",
    ]
    return any(re.search(p, url) for p in patterns)


def parse_git_url(url: str) -> tuple[str, str, str]:
    """Parse a git URL with optional branch/commit spec.

    Supports:
        https://github.com/user/repo
        https://github.com/user/repo@branch
        https://github.com/user/repo#commit
    """
    branch = ""
    commit = ""

    if "#" in url:
        url, commit = url.rsplit("#", 1)
    if "@" in url and not url.startswith("git@"):
        url, branch = url.rsplit("@", 1)

    return url, branch, commit


async def acquire_source(
    source_path: str = "",
    source_repo: str = "",
    output_dir: str = "./audit_output",
) -> AcquisitionResult:
    """Acquire source code from local path or git repo."""
    if source_path:
        return _validate_local_path(source_path)

    if source_repo:
        return await _clone_repo(source_repo, output_dir)

    return AcquisitionResult(success=False, error="No source path or repo URL provided")


def _validate_local_path(path: str) -> AcquisitionResult:
    """Validate that a local path exists and is readable."""
    p = Path(path).resolve()
    if not p.exists():
        return AcquisitionResult(success=False, error=f"Path does not exist: {path}")
    if not p.is_dir():
        return AcquisitionResult(success=False, error=f"Path is not a directory: {path}")
    if not os.access(p, os.R_OK):
        return AcquisitionResult(success=False, error=f"Path is not readable: {path}")
    return AcquisitionResult(success=True, local_path=str(p))


async def _clone_repo(repo_url: str, output_dir: str) -> AcquisitionResult:
    """Clone a git repository."""
    url, branch, commit = parse_git_url(repo_url)

    repo_name = url.rstrip("/").split("/")[-1].replace(".git", "")
    clone_dir = Path(output_dir) / "repos" / repo_name
    clone_dir.parent.mkdir(parents=True, exist_ok=True)

    if clone_dir.exists():
        logger.info(f"Repo already cloned at {clone_dir}, pulling latest")
        cmd = f"cd {clone_dir} && git pull"
        process = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await process.wait()
        return AcquisitionResult(success=True, local_path=str(clone_dir), repo_url=url)

    cmd = ["git", "clone", "--depth=1"]
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([url, str(clone_dir)])

    logger.info(f"Cloning {url} to {clone_dir}")
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error = stderr.decode("utf-8", errors="replace")
        return AcquisitionResult(success=False, error=f"Git clone failed: {error}", repo_url=url)

    if commit:
        checkout_process = await asyncio.create_subprocess_exec(
            "git", "checkout", commit,
            cwd=str(clone_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await checkout_process.wait()

    return AcquisitionResult(
        success=True,
        local_path=str(clone_dir),
        repo_url=url,
        branch=branch,
        commit=commit,
    )
