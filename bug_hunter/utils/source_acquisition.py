"""Source code acquisition — clone repos or validate local paths.

Sources are copied into run-specific directories so each run audits a
stable point-in-time copy. Git repos are cloned and the exact commit hash
is recorded. Local paths are copied via `cp -a` — this is not an atomic
filesystem snapshot, so concurrent modifications to the source during copy
may produce a partially mixed state.
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
    """Acquire source code from local path or git repo(s).

    Both modes create run-scoped copies for stability:
    - Local paths: copied into a run-specific directory via cp -a.
    - Git repos: cloned into a run-specific directory.

    Multiple git repos can be provided as a comma-separated string.
    Each repo is cloned into a subdirectory under a shared parent.
    """
    if source_path:
        return await _snapshot_local_path(source_path, output_dir, run_id)

    if source_repo:
        repos = [r.strip() for r in source_repo.split(",") if r.strip()]
        if len(repos) == 1:
            return await _clone_repo_immutable(repos[0], output_dir, run_id)
        return await _clone_multiple_repos(repos, output_dir, run_id)

    return AcquisitionResult(success=False, error="No source path or repo URL provided")


async def _clone_multiple_repos(
    repo_urls: list[str], output_dir: str, run_id: str,
) -> AcquisitionResult:
    """Clone multiple git repos into a shared parent directory."""
    parent_dir = Path(output_dir) / "repos" / f"multi_{run_id[:8]}" if run_id else Path(output_dir) / "repos" / "multi"
    parent_dir.mkdir(parents=True, exist_ok=True)

    results = []
    errors = []
    for url in repo_urls:
        parsed_url, branch, commit = parse_git_url(url)
        # Use host_owner_repo to avoid collisions across hosts and orgs
        from urllib.parse import urlparse
        parsed = urlparse(parsed_url)
        host = parsed.hostname or "local"
        path_parts = parsed.path.strip("/").replace(".git", "").split("/")
        repo_name = f"{host}_{'_'.join(path_parts)}" if path_parts else host
        # Sanitize for filesystem
        repo_name = re.sub(r'[^\w\-.]', '_', repo_name)
        clone_dir = parent_dir / repo_name

        if clone_dir.exists():
            existing_remote = await _get_git_remote(str(clone_dir))
            existing_commit = await _get_git_commit(str(clone_dir))
            # Reuse only if the remote URL matches and commit is compatible
            if (existing_remote and existing_remote.rstrip("/").rstrip(".git") == parsed_url.rstrip("/").rstrip(".git")
                    and existing_commit and (not commit or existing_commit.startswith(commit))):
                results.append(AcquisitionResult(
                    success=True, local_path=str(clone_dir),
                    repo_url=parsed_url, branch=branch, commit=existing_commit,
                ))
                continue
            import shutil
            shutil.rmtree(clone_dir, ignore_errors=True)

        cmd = ["git", "clone"]
        if branch:
            cmd.extend(["--branch", branch])
        cmd.extend([parsed_url, str(clone_dir)])

        logger.info(f"Cloning {parsed_url} to {clone_dir}")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace")
            errors.append(f"{parsed_url}: {error}")
            continue

        if commit:
            checkout = await asyncio.create_subprocess_exec(
                "git", "checkout", commit,
                cwd=str(clone_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            co_stdout, co_stderr = await checkout.communicate()
            if checkout.returncode != 0:
                error = co_stderr.decode("utf-8", errors="replace")
                import shutil
                shutil.rmtree(clone_dir, ignore_errors=True)
                errors.append(f"{parsed_url}: checkout failed for '{commit}': {error}")
                continue

        actual_commit = await _get_git_commit(str(clone_dir))
        results.append(AcquisitionResult(
            success=True, local_path=str(clone_dir),
            repo_url=parsed_url, branch=branch, commit=actual_commit or commit,
        ))

    if not results:
        return AcquisitionResult(
            success=False,
            error=f"All repos failed to clone: {'; '.join(errors)}",
        )

    # Return the parent directory as the local_path so the bug hunter scans all repos
    all_commits = ", ".join(f"{r.repo_url}@{r.commit[:8]}" for r in results if r.commit)
    return AcquisitionResult(
        success=True,
        local_path=str(parent_dir),
        repo_url=", ".join(r.repo_url for r in results),
        commit=all_commits,
    )


async def _snapshot_local_path(path: str, output_dir: str, run_id: str) -> AcquisitionResult:
    """Create an immutable snapshot of a local source directory.

    Copies the source into a run-specific directory so files cannot change
    during the audit.
    """
    p = Path(path).resolve()
    if not p.exists():
        return AcquisitionResult(success=False, error=f"Path does not exist: {path}")
    if not p.is_dir():
        return AcquisitionResult(success=False, error=f"Path is not a directory: {path}")
    if not os.access(p, os.R_OK):
        return AcquisitionResult(success=False, error=f"Path is not readable: {path}")

    commit = await _get_git_commit(str(p))

    if not run_id:
        # No run_id means we can't snapshot — return live path with warning
        logger.warning("No run_id provided, using live source path (not snapshotted)")
        return AcquisitionResult(success=True, local_path=str(p), commit=commit)

    # Copy source into a run-scoped snapshot directory.
    # A .snapshot_complete marker file distinguishes finished copies from
    # partial ones left behind by interrupted runs.
    dir_name = p.name
    snapshot_dir = Path(output_dir) / "repos" / f"{dir_name}_{run_id[:8]}"
    marker = snapshot_dir / ".snapshot_complete"

    if snapshot_dir.exists():
        if marker.exists():
            logger.info(f"Snapshot already exists at {snapshot_dir}")
            snapshot_commit = await _get_git_commit(str(snapshot_dir))
            return AcquisitionResult(success=True, local_path=str(snapshot_dir), commit=snapshot_commit or commit)
        else:
            # Partial snapshot from a previous interrupted run — remove and redo
            logger.warning(f"Removing incomplete snapshot at {snapshot_dir}")
            import shutil
            shutil.rmtree(snapshot_dir, ignore_errors=True)

    snapshot_dir.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Snapshotting {p} to {snapshot_dir}")

    process = await asyncio.create_subprocess_exec(
        "cp", "-a", str(p), str(snapshot_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        error = stderr.decode("utf-8", errors="replace")
        # Clean up partial copy
        import shutil
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        return AcquisitionResult(success=False, error=f"Snapshot copy failed: {error}")

    # Mark snapshot as complete
    marker.touch()

    return AcquisitionResult(success=True, local_path=str(snapshot_dir), commit=commit)


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
        if existing_commit and (not commit or existing_commit.startswith(commit)):
            logger.info(f"Repo snapshot exists at {clone_dir} (commit: {existing_commit[:8]})")
            return AcquisitionResult(
                success=True, local_path=str(clone_dir), repo_url=url,
                branch=branch, commit=existing_commit,
            )
        import shutil
        shutil.rmtree(clone_dir, ignore_errors=True)

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
        co_stdout, co_stderr = await checkout_process.communicate()
        if checkout_process.returncode != 0:
            error = co_stderr.decode("utf-8", errors="replace")
            import shutil
            shutil.rmtree(clone_dir, ignore_errors=True)
            return AcquisitionResult(
                success=False,
                error=f"Git checkout failed for ref '{commit}': {error}",
                repo_url=url,
            )

    # Record the exact commit hash for this snapshot
    actual_commit = await _get_git_commit(str(clone_dir))
    if commit and actual_commit and not actual_commit.startswith(commit):
        logger.warning(f"Requested commit {commit} resolved to {actual_commit}")

    return AcquisitionResult(
        success=True,
        local_path=str(clone_dir),
        repo_url=url,
        branch=branch,
        commit=actual_commit or commit,
    )


async def _get_git_remote(path: str) -> str:
    """Get the origin remote URL of a git repo, or empty string."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git", "config", "--get", "remote.origin.url",
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
