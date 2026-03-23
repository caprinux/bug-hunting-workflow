"""CLI subprocess wrapper for Claude Code and Codex CLI invocation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class CLIResult:
    success: bool
    result: Optional[dict | list] = None
    raw_output: str = ""
    error: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    session_id: str = ""


@dataclass
class StreamEvent:
    """A single event from streaming CLI output."""
    type: str  # init, assistant, result, error, tool_use, progress
    data: dict = field(default_factory=dict)
    raw: str = ""


async def run_claude(
    prompt: str,
    agent_file: Optional[str] = None,
    model: str = "opus",
    cwd: Optional[str] = None,
    json_schema_file: Optional[str] = None,
    timeout: int = 300,
    on_event: Optional[Callable[[StreamEvent], None]] = None,
    max_budget_usd: Optional[float] = None,
    additional_dirs: Optional[list[str]] = None,
) -> CLIResult:
    """Run Claude Code CLI as a subprocess and capture output.

    Args:
        prompt: The task prompt to send.
        agent_file: Path to agent markdown file for --append-system-prompt-file.
        model: Model name (opus, sonnet, haiku).
        cwd: Working directory for the subprocess.
        json_schema_file: Path to JSON schema file for structured output.
        timeout: Timeout in seconds.
        on_event: Callback for streaming events.
        max_budget_usd: Maximum API spend.
        additional_dirs: Additional directories to grant access to.
    """
    cmd = ["claude", "--print", "--output-format", "stream-json", "--verbose",
           "--dangerously-skip-permissions", "--model", model, "--no-session-persistence"]

    if agent_file:
        cmd.extend(["--append-system-prompt-file", str(Path(agent_file).resolve())])

    if json_schema_file:
        with open(json_schema_file) as f:
            schema = f.read()
        cmd.extend(["--json-schema", schema])

    if max_budget_usd:
        cmd.extend(["--max-budget-usd", str(max_budget_usd)])

    if additional_dirs:
        cmd.extend(["--add-dir"] + additional_dirs)

    cmd.append(prompt)

    env = os.environ.copy()
    env["IS_SANDBOX"] = "1"

    return await _run_cli_process(cmd, env, cwd, timeout, on_event)


async def run_codex(
    prompt: str,
    model: str = "o3",
    cwd: Optional[str] = None,
    timeout: int = 300,
    on_event: Optional[Callable[[StreamEvent], None]] = None,
    additional_dirs: Optional[list[str]] = None,
) -> CLIResult:
    """Run Codex CLI as a subprocess and capture output."""
    cmd = ["codex", "exec",
           "--dangerously-bypass-approvals-and-sandbox",
           "--json", "--ephemeral", "--skip-git-repo-check",
           "-m", model]

    if cwd:
        cmd.extend(["-C", cwd])

    if additional_dirs:
        for d in additional_dirs:
            cmd.extend(["--add-dir", d])

    cmd.append(prompt)

    return await _run_cli_process(cmd, None, cwd if not cwd else None, timeout, on_event)


async def _run_cli_process(
    cmd: list[str],
    env: Optional[dict],
    cwd: Optional[str],
    timeout: int,
    on_event: Optional[Callable[[StreamEvent], None]],
) -> CLIResult:
    """Run a CLI process, stream events, and return the result."""
    start_time = time.monotonic()
    raw_lines: list[str] = []
    result_data = None
    cost_usd = 0.0
    session_id = ""
    error_msg = ""

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        async def read_stream():
            nonlocal result_data, cost_usd, session_id
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                raw_lines.append(line_str)

                try:
                    event_data = json.loads(line_str)
                    event = _parse_stream_event(event_data)

                    if event.type == "result":
                        result_data = event.data
                        cost_usd = event.data.get("total_cost_usd", 0.0)
                        session_id = event.data.get("session_id", "")

                    if on_event:
                        on_event(event)

                except json.JSONDecodeError:
                    if on_event:
                        on_event(StreamEvent(type="log", data={"text": line_str}, raw=line_str))

        try:
            await asyncio.wait_for(read_stream(), timeout=timeout)
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            error_msg = f"Process timed out after {timeout}s"

        stderr_data = await process.stderr.read()
        stderr_str = stderr_data.decode("utf-8", errors="replace").strip()
        if stderr_str and not error_msg:
            error_msg = stderr_str

        duration_ms = int((time.monotonic() - start_time) * 1000)

        if result_data:
            parsed_result = result_data.get("result")
            if isinstance(parsed_result, str):
                try:
                    parsed_result = json.loads(parsed_result)
                except (json.JSONDecodeError, TypeError):
                    pass
            return CLIResult(
                success=not result_data.get("is_error", False),
                result=parsed_result,
                raw_output="\n".join(raw_lines),
                error=error_msg,
                duration_ms=duration_ms,
                cost_usd=cost_usd,
                session_id=session_id,
            )

        return CLIResult(
            success=False,
            raw_output="\n".join(raw_lines),
            error=error_msg or "No result received from CLI",
            duration_ms=duration_ms,
            cost_usd=cost_usd,
        )

    except FileNotFoundError as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return CLIResult(
            success=False,
            error=f"CLI not found: {e}",
            duration_ms=duration_ms,
        )
    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return CLIResult(
            success=False,
            error=str(e),
            duration_ms=duration_ms,
        )


def _parse_stream_event(data: dict) -> StreamEvent:
    """Parse a raw JSON event from Claude/Codex stream output."""
    event_type = data.get("type", "unknown")

    if event_type == "result":
        return StreamEvent(type="result", data=data)
    elif event_type in ("assistant", "message"):
        return StreamEvent(type="assistant", data=data)
    elif event_type == "init":
        return StreamEvent(type="init", data=data)
    elif event_type == "error":
        return StreamEvent(type="error", data=data)
    else:
        return StreamEvent(type=event_type, data=data)


async def check_cli_available(cli: str) -> bool:
    """Check if a CLI tool is available."""
    try:
        process = await asyncio.create_subprocess_exec(
            "which", cli,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.wait()
        return process.returncode == 0
    except Exception:
        return False
