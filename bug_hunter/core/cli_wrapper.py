"""CLI subprocess wrapper for Claude Code and Codex CLI invocation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)

CODEX_MODELS = {"gpt-5.4", "o3", "o4-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"}


def is_codex_model(model: str) -> bool:
    """Check if a model string refers to a Codex/OpenAI model."""
    return model in CODEX_MODELS or model.startswith("gpt-") or model.startswith("o3") or model.startswith("o4")


async def run_agent(
    prompt: str,
    model: str = "opus",
    agent_file: Optional[str] = None,
    cwd: Optional[str] = None,
    json_schema_file: Optional[str] = None,
    timeout: int = 300,
    on_event: Optional[Callable] = None,
    additional_dirs: Optional[list[str]] = None,
    record_dir: Optional[str] = None,
    record_metadata: Optional[dict] = None,
    session_id: Optional[str] = None,
    is_resume: bool = False,
):
    """Dispatch to run_claude or run_codex based on the model string."""
    if is_codex_model(model):
        return await run_codex(
            prompt=prompt,
            model=model,
            cwd=cwd,
            timeout=timeout,
            on_event=on_event,
            additional_dirs=additional_dirs,
            record_dir=record_dir,
            record_metadata=record_metadata,
            output_schema_file=json_schema_file,
            thread_id=session_id if is_resume else None,
        )
    else:
        return await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=model,
            cwd=cwd,
            json_schema_file=json_schema_file,
            timeout=timeout,
            on_event=on_event,
            additional_dirs=additional_dirs,
            record_dir=record_dir,
            record_metadata=record_metadata,
            session_id=session_id,
            is_resume=is_resume,
        )


def _parse_result_payload(payload: Any) -> Any:
    """Best-effort normalization for CLI payloads that should contain JSON."""
    if not isinstance(payload, str):
        return payload

    text = payload.strip()
    if not text:
        return text

    candidates = [text]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:json)?\s*(.*?)```",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if match.group(1).strip()
    )

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue

    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[idx:])
            return parsed
        except json.JSONDecodeError:
            continue

    return payload


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    return repr(value)


def _write_json(path: str, payload: dict) -> None:
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=_json_default)


def _write_text(path: str, content: str) -> None:
    with open(path, "w") as f:
        f.write(content)


def _append_jsonl(path: str, payload: dict) -> None:
    with open(path, "a") as f:
        json.dump(payload, f, default=_json_default)
        f.write("\n")


@dataclass
class CLIResult:
    success: bool
    result: Optional[Any] = None
    raw_output: str = ""
    error: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    session_id: str = ""
    usage: Optional[dict] = None  # token usage: input_tokens, output_tokens, etc.


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
    record_dir: Optional[str] = None,
    record_metadata: Optional[dict] = None,
    session_id: Optional[str] = None,
    is_resume: bool = False,
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
        session_id: Optional session ID for persistent multi-turn sessions.
        is_resume: If True, resume an existing session instead of starting new.
    """
    cmd = ["claude", "--print", "--output-format", "stream-json", "--verbose",
           "--dangerously-skip-permissions", "--model", model]

    if session_id:
        if is_resume:
            cmd.extend(["--resume", session_id])
        else:
            cmd.extend(["--session-id", session_id])
    else:
        cmd.append("--no-session-persistence")

    if agent_file:
        cmd.extend(["--append-system-prompt-file", str(Path(agent_file).resolve())])

    if json_schema_file:
        with open(json_schema_file) as f:
            schema = f.read()
        cmd.extend(["--json-schema", schema])

    if max_budget_usd:
        cmd.extend(["--max-budget-usd", str(max_budget_usd)])

    # Prompt must come BEFORE --add-dir (variadic flag consumes remaining args)
    cmd.append(prompt)

    if additional_dirs:
        for d in additional_dirs:
            cmd.extend(["--add-dir", d])

    env = os.environ.copy()
    env["IS_SANDBOX"] = "1"

    command_preview = list(cmd)
    if "--json-schema" in command_preview:
        schema_idx = command_preview.index("--json-schema") + 1
        if schema_idx < len(command_preview):
            command_preview[schema_idx] = "<json_schema>"
    if command_preview:
        command_preview[-1] = "<prompt>"

    return await _run_cli_process(
        cmd, env, cwd, timeout, on_event,
        prompt=prompt,
        record_dir=record_dir,
        record_request={
            "cli": "claude",
            "model": model,
            "agent_file": str(Path(agent_file).resolve()) if agent_file else "",
            "json_schema_file": str(Path(json_schema_file).resolve()) if json_schema_file else "",
            "max_budget_usd": max_budget_usd,
            "additional_dirs": additional_dirs or [],
            "command_preview": command_preview,
            "metadata": record_metadata or {},
        },
    )


async def run_claude_chat(
    prompt: str,
    session_id: str,
    is_resume: bool = False,
    system_prompt: str = "",
    model: str = "sonnet",
    timeout: int = 120,
    on_event: Optional[Callable[[StreamEvent], None]] = None,
    cwd: Optional[str] = None,
    additional_dirs: Optional[list[str]] = None,
) -> CLIResult:
    """Run Claude Code CLI for chat — with session persistence for multi-turn.

    Uses --session-id on the first message and --resume on subsequent messages
    so Claude maintains conversation context across turns.
    """
    cmd = ["claude", "--print", "--output-format", "stream-json", "--verbose",
           "--dangerously-skip-permissions", "--model", model]

    if is_resume:
        cmd.extend(["--resume", session_id])
    else:
        cmd.extend(["--session-id", session_id])

    if system_prompt and not is_resume:
        cmd.extend(["--system-prompt", system_prompt])

    # Prompt must come BEFORE --add-dir (variadic flag consumes remaining args)
    cmd.append(prompt)

    if additional_dirs:
        for d in additional_dirs:
            cmd.extend(["--add-dir", d])

    env = os.environ.copy()
    env["IS_SANDBOX"] = "1"

    return await _run_cli_process(
        cmd, env, cwd, timeout, on_event,
        prompt=prompt,
        record_dir=None,
        record_request=None,
    )


async def run_codex(
    prompt: str,
    model: str = "gpt-5.4",
    cwd: Optional[str] = None,
    timeout: int = 300,
    on_event: Optional[Callable[[StreamEvent], None]] = None,
    additional_dirs: Optional[list[str]] = None,
    record_dir: Optional[str] = None,
    record_metadata: Optional[dict] = None,
    output_schema_file: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> CLIResult:
    """Run Codex CLI as a subprocess and capture output."""
    if thread_id:
        # Resume an existing thread
        cmd = ["codex", "resume",
               "--dangerously-bypass-approvals-and-sandbox",
               "--json", "--skip-git-repo-check",
               thread_id]
        # For resume, prompt is the new message
        cmd.append(prompt)
    else:
        cmd = ["codex", "exec",
               "--dangerously-bypass-approvals-and-sandbox",
               "--json", "--skip-git-repo-check",
               "-m", model]

        if cwd:
            cmd.extend(["-C", cwd])

        if output_schema_file:
            cmd.extend(["--output-schema", str(Path(output_schema_file).resolve())])

        # Prompt must come BEFORE --add-dir (variadic flag consumes remaining args)
        cmd.append(prompt)

    if additional_dirs:
        for d in additional_dirs:
            cmd.extend(["--add-dir", d])

    command_preview = list(cmd)
    if command_preview:
        command_preview[-1] = "<prompt>"

    return await _run_cli_process(
        cmd, None, cwd if not cwd else None, timeout, on_event,
        prompt=prompt,
        record_dir=record_dir,
        record_request={
            "cli": "codex",
            "model": model,
            "additional_dirs": additional_dirs or [],
            "command_preview": command_preview,
            "metadata": record_metadata or {},
        },
    )


async def _run_cli_process(
    cmd: list[str],
    env: Optional[dict],
    cwd: Optional[str],
    timeout: int,
    on_event: Optional[Callable[[StreamEvent], None]],
    prompt: str,
    record_dir: Optional[str] = None,
    record_request: Optional[dict] = None,
) -> CLIResult:
    """Run a CLI process, stream events, and return the result."""
    start_time = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    raw_lines: list[str] = []
    result_data = None
    cost_usd = 0.0
    session_id = ""
    error_msg = ""

    if record_dir:
        os.makedirs(record_dir, exist_ok=True)
        _write_text(os.path.join(record_dir, "prompt.txt"), prompt)
        _write_json(
            os.path.join(record_dir, "request.json"),
            {
                **(record_request or {}),
                "cwd": cwd or "",
                "timeout_seconds": timeout,
                "started_at": started_at,
            },
        )

    def finalize_record(result: CLIResult) -> CLIResult:
        if not record_dir:
            return result

        stderr_path = os.path.join(record_dir, "stderr.txt")
        result_path = os.path.join(record_dir, "result.json")
        _write_text(stderr_path, result.error if result.error and not os.path.exists(stderr_path) else "")
        _write_json(
            result_path,
            {
                "success": result.success,
                "result": result.result,
                "error": result.error,
                "duration_ms": result.duration_ms,
                "cost_usd": result.cost_usd,
                "session_id": result.session_id,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "raw_output_line_count": len(raw_lines),
            },
        )
        return result

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            limit=10 * 1024 * 1024,  # 10MB readline buffer (default 64KB is too small for CLI output)
        )

        # Track Codex agent_message items to reconstruct the final result
        codex_messages: list[str] = []
        stderr_chunks: list[bytes] = []

        async def drain_stderr():
            """Drain stderr concurrently to prevent pipe buffer deadlock."""
            while True:
                chunk = await process.stderr.read(65536)
                if not chunk:
                    break
                stderr_chunks.append(chunk)

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
                if record_dir:
                    _append_jsonl(
                        os.path.join(record_dir, "stream.jsonl"),
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "stream": "stdout",
                            "raw": line_str,
                        },
                    )

                try:
                    event_data = json.loads(line_str)
                    event = _parse_stream_event(event_data)

                    # Claude Code format: "result" event with result field
                    # Multiple result events may arrive (e.g. background tasks completing).
                    # Preserve structured_output from the first event that has it.
                    if event.type == "result":
                        prev_structured = result_data.get("structured_output") if result_data else None
                        result_data = event.data
                        if prev_structured and not result_data.get("structured_output"):
                            result_data["structured_output"] = prev_structured
                        cost_usd = event.data.get("total_cost_usd", 0.0)
                        session_id = event.data.get("session_id", "")

                    # Codex format: "thread.started" with thread_id
                    elif event.type == "thread.started":
                        session_id = event.data.get("thread_id", "")

                    # Codex format: "item.completed" with agent_message text
                    elif event.type == "item.completed":
                        item = event.data.get("item", {})
                        if item.get("type") == "agent_message":
                            codex_messages.append(item.get("text", ""))

                    # Codex format: "turn.completed" with usage stats
                    elif event.type == "turn.completed":
                        usage = event.data.get("usage", {})
                        # Build a result from collected Codex messages
                        if not result_data and codex_messages:
                            last_message = codex_messages[-1]
                            # Try to parse the last message as JSON
                            try:
                                parsed = json.loads(last_message)
                                result_data = {"result": parsed, "is_error": False}
                            except (json.JSONDecodeError, TypeError):
                                # Codex sometimes produces malformed JSON (single quotes,
                                # truncated output). Try to repair common issues.
                                try:
                                    repaired = last_message.replace("'", '"')
                                    parsed = json.loads(repaired)
                                    result_data = {"result": parsed, "is_error": False}
                                except (json.JSONDecodeError, TypeError):
                                    result_data = {"result": last_message, "is_error": False}

                    if on_event:
                        on_event(event)

                except json.JSONDecodeError:
                    if on_event:
                        on_event(StreamEvent(type="log", data={"text": line_str}, raw=line_str))

        stderr_task = asyncio.create_task(drain_stderr())
        try:
            await asyncio.wait_for(read_stream(), timeout=timeout)
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            error_msg = f"Process timed out after {timeout}s"

        # Ensure stderr drain completes
        try:
            await asyncio.wait_for(stderr_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            stderr_task.cancel()

        stderr_str = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
        if record_dir:
            _write_text(os.path.join(record_dir, "stderr.txt"), stderr_str)
            for line in stderr_str.splitlines():
                _append_jsonl(
                    os.path.join(record_dir, "stream.jsonl"),
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "stream": "stderr",
                        "raw": line,
                    },
                )
        if stderr_str and not error_msg:
            error_msg = stderr_str

        duration_ms = int((time.monotonic() - start_time) * 1000)

        if result_data:
            # Extract error messages from result event (Claude puts them in 'errors' array)
            if not error_msg and result_data.get("errors"):
                error_msg = "; ".join(str(e) for e in result_data["errors"])

            # Prefer structured_output (from --json-schema) over raw result text
            structured = result_data.get("structured_output")
            if structured and isinstance(structured, dict):
                parsed_result = structured
            else:
                parsed_result = _parse_result_payload(result_data.get("result"))
            usage = result_data.get("usage")
            return finalize_record(CLIResult(
                success=not result_data.get("is_error", False),
                result=parsed_result,
                raw_output="\n".join(raw_lines),
                error=error_msg,
                duration_ms=duration_ms,
                cost_usd=cost_usd,
                session_id=session_id,
                usage=usage,
            ))

        return finalize_record(CLIResult(
            success=False,
            raw_output="\n".join(raw_lines),
            error=error_msg or "No result received from CLI",
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            session_id=session_id,
        ))

    except asyncio.CancelledError:
        try:
            if "process" in locals() and process.returncode is None:
                process.kill()
                await process.wait()
        except Exception:
            pass
        if record_dir:
            _write_json(
                os.path.join(record_dir, "result.json"),
                {
                    "success": False,
                    "error": "Cancelled",
                    "duration_ms": int((time.monotonic() - start_time) * 1000),
                    "cost_usd": cost_usd,
                    "session_id": session_id,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "raw_output_line_count": len(raw_lines),
                },
            )
        raise
    except FileNotFoundError as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return finalize_record(CLIResult(
            success=False,
            error=f"CLI not found: {e}",
            duration_ms=duration_ms,
        ))
    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return finalize_record(CLIResult(
            success=False,
            error=str(e),
            duration_ms=duration_ms,
        ))


def _parse_stream_event(data: dict) -> StreamEvent:
    """Parse a raw JSON event from Claude/Codex stream output."""
    event_type = data.get("type", "unknown")

    if event_type == "result":
        return StreamEvent(type="result", data=data)
    elif event_type in (
        "assistant",
        "message",
        "message_delta",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
    ):
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
