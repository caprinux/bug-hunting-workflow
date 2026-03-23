"""Source code acquisition — clone repos or validate local paths.

Sources are treated as immutable snapshots per run. Git repos are cloned into
run-specific directories and the exact commit hash is recorded.
"""

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
    run_id: str = "",
) -> AcquisitionResult:
    """Acquire source code from local path or git repo.

    For local paths: validates and records the current git commit if available.
    For git repos: clones into a run-specific directory (immutable snapshot).
    """
    if source_path:
        return await _snapshot_local_path(source_path)

    if source_repo:
        return await _clone_repo_immutable(source_repo, output_dir, run_id)

    return AcquisitionResult(success=False, error="No source path or repo URL provided")


async def _snapshot_local_path(path: str) -> AcquisitionResult:
    """Validate a local path and record its git commit if it's a repo."""
    p = Path(path).resolve()
    if not p.exists():
        return AcquisitionResult(success=False, error=f"Path does not exist: {path}")
    if not p.is_dir():
        return AcquisitionResult(success=False, error=f"Path is not a directory: {path}")
    if not os.access(p, os.R_OK):
        return AcquisitionResult(success=False, error=f"Path is not readable: {path}")

    commit = await _get_git_commit(str(p))
    return AcquisitionResult(success=True, local_path=str(p), commit=commit)


async def _clone_repo_immutable(repo_url: str, output_dir: str, run_id: str) -> AcquisitionResult:
    """Clone a git repository into a run-specific immutable directory."""
    url, branch, commit = parse_git_url(repo_url)

    repo_name = url.rstrip("/").split("/")[-1].replace(".git", "")
    # Each run gets its own clone to prevent drift between runs
    clone_dir = Path(output_dir) / "repos" / f"{repo_name}_{run_id[:8]}" if run_id else Path(output_dir) / "repos" / repo_name
    clone_dir.parent.mkdir(parents=True, exist_ok=True)

    if clone_dir.exists():
        # Run-specific dir already exists (resume case) — verify commit
        existing_commit = await _get_git_commit(str(clone_dir))
        if existing_commit:
            logger.info(f"Repo snapshot exists at {clone_dir} (commit: {existing_commit[:8]})")
            return AcquisitionResult(
                success=True, local_path=str(clone_dir), repo_url=url,
                branch=branch, commit=existing_commit,
            )

    cmd = ["git", "clone"]
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

    # Record the exact commit hash for this snapshot
    actual_commit = await _get_git_commit(str(clone_dir))

    return AcquisitionResult(
        success=True,
        local_path=str(clone_dir),
        repo_url=url,
        branch=branch,
        commit=actual_commit or commit,
    )


async def _get_git_commit(path: str) -> str:
    """Get the current git commit hash of a directory, or empty string if not a git repo."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode == 0:
            return stdout.decode().strip()
    except Exception:
        pass
    return ""
