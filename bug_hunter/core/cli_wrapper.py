"""SDK wrapper for Claude, Codex, Copilot, and OpenCode agent invocation."""

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
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

CODEX_MODELS = {"gpt-5.5", "gpt-5.4", "o3", "o4-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"}


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
        dev_instructions = None
        if agent_file and not is_resume:
            with open(agent_file) as f:
                dev_instructions = f.read()
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
            developer_instructions=dev_instructions,
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


# --- Utilities ---

def _parse_result_payload(payload: Any) -> Any:
    """Best-effort normalization for payloads that should contain JSON."""
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
    usage: Optional[dict] = None


@dataclass
class StreamEvent:
    """A single event from streaming output."""
    type: str  # init, assistant, result, error, tool_use, progress
    data: dict = field(default_factory=dict)
    raw: str = ""


# --- Claude SDK ---

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
    """Run Claude Code via the claude-agent-sdk."""
    from claude_agent_sdk import (
        ClaudeSDKClient, ClaudeAgentOptions,
        AssistantMessage, UserMessage, ResultMessage,
        TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock,
    )

    start_time = time.monotonic()
    raw_lines: list[str] = []
    cost_usd = 0.0
    result_session_id = session_id or ""
    error_msg = ""
    accumulated_text: list[str] = []
    structured_output = None
    usage = None

    if record_dir:
        os.makedirs(record_dir, exist_ok=True)
        _write_text(os.path.join(record_dir, "prompt.txt"), prompt)
        _write_json(os.path.join(record_dir, "request.json"), {
            "cli": "claude-sdk",
            "model": model,
            "agent_file": str(Path(agent_file).resolve()) if agent_file else "",
            "json_schema_file": str(Path(json_schema_file).resolve()) if json_schema_file else "",
            "session_id": session_id or "",
            "is_resume": is_resume,
            "metadata": record_metadata or {},
            "cwd": cwd or "",
            "timeout_seconds": timeout,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })

    def finalize(result: CLIResult) -> CLIResult:
        if not record_dir:
            return result
        _write_json(os.path.join(record_dir, "result.json"), {
            "success": result.success,
            "result": result.result,
            "error": result.error,
            "duration_ms": result.duration_ms,
            "cost_usd": result.cost_usd,
            "session_id": result.session_id,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        return result

    try:
        # Build options
        stderr_lines: list[str] = []
        opts_kwargs = {
            "permission_mode": "bypassPermissions",
            "model": model,
            "stderr": lambda line: stderr_lines.append(line),
            "env": {"IS_SANDBOX": "1"},
        }
        if cwd:
            opts_kwargs["cwd"] = cwd
        if session_id and is_resume:
            opts_kwargs["resume"] = session_id
        elif session_id:
            opts_kwargs["session_id"] = session_id

        # System prompt from agent file
        if agent_file and not is_resume:
            with open(agent_file) as f:
                system_prompt = f.read()
            opts_kwargs["system_prompt"] = system_prompt

        if json_schema_file:
            with open(json_schema_file) as f:
                schema = json.loads(f.read())
            opts_kwargs["output_format"] = {"type": "json_schema", "schema": schema}

        if additional_dirs:
            opts_kwargs["add_dirs"] = additional_dirs

        if max_budget_usd:
            opts_kwargs["max_budget_usd"] = max_budget_usd

        options = ClaudeAgentOptions(**opts_kwargs)
        client = ClaudeSDKClient(options)
        await client.connect(prompt)

        async def process_messages():
            nonlocal cost_usd, result_session_id, structured_output, usage
            async for msg in client.receive_messages():
                events_to_record: list[dict] = []

                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            accumulated_text.append(block.text)
                            evt = {"type": "assistant", "message": {"content": [{"type": "text", "text": block.text}]}}
                            events_to_record.append(evt)
                            if on_event:
                                on_event(StreamEvent(type="assistant", data=evt))
                        elif isinstance(block, ThinkingBlock):
                            evt = {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": block.thinking}}
                            events_to_record.append(evt)
                            if on_event:
                                on_event(StreamEvent(type="assistant", data=evt))
                        elif isinstance(block, ToolUseBlock):
                            evt = {"type": "tool_use", "name": block.name, "input": block.input}
                            events_to_record.append(evt)
                            if on_event:
                                on_event(StreamEvent(type="tool_use", data=evt))
                            # Capture StructuredOutput tool call as fallback
                            if block.name == "StructuredOutput" and isinstance(block.input, dict):
                                structured_output = block.input

                elif isinstance(msg, UserMessage):
                    for block in msg.content:
                        if isinstance(block, ToolResultBlock):
                            evt = {"type": "tool_result", "content": str(block.content)[:500]}
                            events_to_record.append(evt)

                elif isinstance(msg, ResultMessage):
                    cost_usd = getattr(msg, "total_cost_usd", 0.0) or 0.0
                    result_session_id = getattr(msg, "session_id", session_id) or session_id or ""
                    structured_output = getattr(msg, "structured_output", None)
                    usage = getattr(msg, "usage", None)
                    if isinstance(usage, dict):
                        pass
                    elif usage:
                        usage = vars(usage) if hasattr(usage, "__dict__") else None
                    evt = {
                        "type": "result", "subtype": "success",
                        "is_error": getattr(msg, "is_error", False),
                        "total_cost_usd": cost_usd,
                        "session_id": result_session_id,
                        "result": getattr(msg, "result", ""),
                        "structured_output": structured_output,
                    }
                    events_to_record.append(evt)
                    if on_event:
                        on_event(StreamEvent(type="result", data=evt))
                    break

                for evt in events_to_record:
                    raw_str = json.dumps(evt, default=str)
                    raw_lines.append(raw_str)
                    if record_dir:
                        _append_jsonl(os.path.join(record_dir, "stream.jsonl"), {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "stream": "stdout",
                            "raw": raw_str,
                        })

        await asyncio.wait_for(process_messages(), timeout=timeout)
        await client.disconnect()

        duration_ms = int((time.monotonic() - start_time) * 1000)

        # Build result
        full_text = "".join(accumulated_text)
        if structured_output and isinstance(structured_output, dict):
            parsed_result = structured_output
        else:
            parsed_result = _parse_result_payload(full_text) if full_text else None

        return finalize(CLIResult(
            success=True,
            result=parsed_result,
            raw_output="\n".join(raw_lines),
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            session_id=result_session_id,
            usage=usage if isinstance(usage, dict) else None,
        ))

    except asyncio.TimeoutError:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        try:
            await client.disconnect()
        except Exception:
            pass
        return finalize(CLIResult(
            success=False,
            error=f"Timed out after {timeout}s",
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            session_id=result_session_id,
        ))
    except asyncio.CancelledError:
        try:
            await client.disconnect()
        except Exception:
            pass
        if record_dir:
            _write_json(os.path.join(record_dir, "result.json"), {
                "success": False, "error": "Cancelled",
                "duration_ms": int((time.monotonic() - start_time) * 1000),
                "cost_usd": cost_usd, "session_id": result_session_id,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
        raise
    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        stderr_text = "\n".join(stderr_lines).strip()
        error_msg = str(e)
        if stderr_text:
            error_msg = f"{error_msg}\nStderr: {stderr_text[-500:]}"
        logger.error(f"Claude SDK error: {error_msg}")
        return finalize(CLIResult(
            success=False,
            error=error_msg,
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            session_id=result_session_id,
        ))


# --- Claude Chat (uses same SDK with session persistence) ---

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
    """Run Claude Code for chat with session persistence."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, ResultMessage, TextBlock, ThinkingBlock, ToolUseBlock

    start_time = time.monotonic()
    raw_lines: list[str] = []
    cost_usd = 0.0
    result_session_id = session_id
    accumulated_text: list[str] = []
    structured_output = None
    usage = None

    try:
        opts_kwargs: dict[str, Any] = {
            "permission_mode": "bypassPermissions",
            "model": model,
            "env": {"IS_SANDBOX": "1"},
        }
        if cwd:
            opts_kwargs["cwd"] = cwd
        if additional_dirs:
            opts_kwargs["add_dirs"] = additional_dirs
        if session_id and is_resume:
            opts_kwargs["resume"] = session_id
        else:
            opts_kwargs["session_id"] = session_id
        if system_prompt and not is_resume:
            opts_kwargs["system_prompt"] = system_prompt

        options = ClaudeAgentOptions(**opts_kwargs)
        client = ClaudeSDKClient(options)
        await client.connect(prompt)

        async def process_messages():
            nonlocal cost_usd, result_session_id, structured_output, usage
            async for msg in client.receive_messages():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            accumulated_text.append(block.text)
                            if on_event:
                                on_event(StreamEvent(type="assistant", data={
                                    "type": "assistant",
                                    "message": {"content": [{"type": "text", "text": block.text}]},
                                }))
                        elif isinstance(block, ThinkingBlock):
                            if on_event:
                                on_event(StreamEvent(type="assistant", data={
                                    "type": "content_block_delta",
                                    "delta": {"type": "thinking_delta", "thinking": block.thinking},
                                }))
                        elif isinstance(block, ToolUseBlock):
                            if on_event:
                                on_event(StreamEvent(type="tool_use", data={
                                    "type": "tool_use", "name": block.name, "input": block.input,
                                }))
                elif isinstance(msg, ResultMessage):
                    cost_usd = getattr(msg, "total_cost_usd", 0.0) or 0.0
                    result_session_id = getattr(msg, "session_id", session_id) or session_id
                    structured_output = getattr(msg, "structured_output", None)
                    usage = getattr(msg, "usage", None)
                    if on_event:
                        on_event(StreamEvent(type="result", data={"type": "result"}))
                    break

        if timeout:
            await asyncio.wait_for(process_messages(), timeout=timeout)
        else:
            await process_messages()
        await client.disconnect()

        duration_ms = int((time.monotonic() - start_time) * 1000)
        full_text = "".join(accumulated_text)
        parsed_result = _parse_result_payload(full_text) if full_text else None

        return CLIResult(
            success=True, result=parsed_result, raw_output=full_text,
            duration_ms=duration_ms, cost_usd=cost_usd,
            session_id=result_session_id,
            usage=vars(usage) if usage and hasattr(usage, "__dict__") and not isinstance(usage, dict) else usage,
        )

    except asyncio.TimeoutError:
        try:
            await client.disconnect()
        except Exception:
            pass
        return CLIResult(
            success=False, error=f"Timed out after {timeout}s",
            duration_ms=int((time.monotonic() - start_time) * 1000),
            session_id=result_session_id,
        )
    except asyncio.CancelledError:
        try:
            await client.disconnect()
        except Exception:
            pass
        raise
    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.error(f"Claude chat error: {e}")
        return CLIResult(
            success=False, error=str(e),
            duration_ms=duration_ms, session_id=result_session_id,
        )


# --- Codex (via codex-agent-sdk) ---

async def run_codex(
    prompt: str,
    model: str = "gpt-5.5",
    cwd: Optional[str] = None,
    timeout: int = 300,
    on_event: Optional[Callable[[StreamEvent], None]] = None,
    additional_dirs: Optional[list[str]] = None,
    record_dir: Optional[str] = None,
    record_metadata: Optional[dict] = None,
    output_schema_file: Optional[str] = None,
    thread_id: Optional[str] = None,
    developer_instructions: Optional[str] = None,
) -> CLIResult:
    """Run Codex via the codex-agent-sdk. Each call spawns a subprocess — safe for concurrency."""
    from codex_agent_sdk import (
        CodexSDKClient, CodexAgentOptions, SandboxMode, ApprovalPolicy,
        ItemCompletedEvent, TurnCompletedEvent, TurnFailedEvent,
        ThreadStartedEvent, StreamErrorEvent,
        AgentMessageItem, CommandExecutionItem, ReasoningItem,
    )

    start_time = time.monotonic()
    raw_lines: list[str] = []
    result_thread_id = thread_id or ""
    codex_messages: list[str] = []
    usage_dict: Optional[dict] = None

    if record_dir:
        os.makedirs(record_dir, exist_ok=True)
        _write_text(os.path.join(record_dir, "prompt.txt"), prompt)
        _write_json(os.path.join(record_dir, "request.json"), {
            "cli": "codex-sdk", "model": model, "thread_id": thread_id or "",
            "additional_dirs": additional_dirs or [], "metadata": record_metadata or {},
            "cwd": cwd or "", "timeout_seconds": timeout,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })

    def finalize(result: CLIResult) -> CLIResult:
        if not record_dir:
            return result
        _write_json(os.path.join(record_dir, "result.json"), {
            "success": result.success, "result": result.result,
            "error": result.error, "duration_ms": result.duration_ms,
            "cost_usd": result.cost_usd, "session_id": result.session_id,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        return result

    try:
        opts = CodexAgentOptions(
            model=model,
            sandbox=SandboxMode.FULL_ACCESS,
            approval_policy=ApprovalPolicy.NEVER,
            cwd=cwd,
            additional_writable_dirs=list(additional_dirs or []),
            output_schema_file=output_schema_file,
            skip_git_repo_check=True,
            ephemeral=not thread_id,
            developer_instructions=developer_instructions,
        )

        client = CodexSDKClient(opts)

        async def _stream():
            nonlocal result_thread_id, usage_dict
            async for event in client.run_streamed(prompt, resume_thread_id=thread_id):
                raw_str = json.dumps({"type": type(event).__name__, "data": str(event)[:500]}, default=str)
                raw_lines.append(raw_str)
                if record_dir:
                    _append_jsonl(os.path.join(record_dir, "stream.jsonl"), {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "stream": "sdk", "raw": raw_str,
                    })

                if isinstance(event, ThreadStartedEvent):
                    result_thread_id = event.thread_id or result_thread_id

                elif isinstance(event, ItemCompletedEvent):
                    item = event.item
                    if isinstance(item, AgentMessageItem):
                        codex_messages.append(item.text)
                        if on_event:
                            on_event(StreamEvent(type="assistant", data={
                                "type": "assistant",
                                "message": {"content": [{"type": "text", "text": item.text}]},
                            }))
                    elif isinstance(item, CommandExecutionItem):
                        if on_event:
                            on_event(StreamEvent(type="tool_use", data={
                                "type": "tool_use", "name": "Bash",
                                "input": {"command": item.command},
                                "output": (item.aggregated_output or "")[:500],
                            }))
                    elif isinstance(item, ReasoningItem):
                        if on_event:
                            on_event(StreamEvent(type="assistant", data={
                                "type": "content_block_delta",
                                "delta": {"type": "thinking_delta", "thinking": item.text},
                            }))

                elif isinstance(event, TurnCompletedEvent):
                    if event.usage:
                        usage_dict = {
                            "input_tokens": event.usage.input_tokens,
                            "output_tokens": event.usage.output_tokens,
                            "cached_input_tokens": event.usage.cached_input_tokens,
                        }
                    if on_event:
                        on_event(StreamEvent(type="result", data={"type": "result"}))

                elif isinstance(event, TurnFailedEvent):
                    raise RuntimeError(event.error.message)

                elif isinstance(event, StreamErrorEvent):
                    raise RuntimeError(event.message)

        if timeout:
            await asyncio.wait_for(_stream(), timeout=timeout)
        else:
            await _stream()

        duration_ms = int((time.monotonic() - start_time) * 1000)

        parsed_result = None
        if codex_messages:
            last_msg = codex_messages[-1]
            try:
                parsed_result = json.loads(last_msg)
            except (json.JSONDecodeError, TypeError):
                parsed_result = _parse_result_payload(last_msg)

        return finalize(CLIResult(
            success=True, result=parsed_result,
            raw_output="\n".join(raw_lines),
            duration_ms=duration_ms, cost_usd=0.0,
            session_id=result_thread_id,
            usage=usage_dict,
        ))

    except asyncio.TimeoutError:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return finalize(CLIResult(
            success=False, error=f"Timed out after {timeout}s",
            duration_ms=duration_ms, session_id=result_thread_id,
        ))
    except asyncio.CancelledError:
        if record_dir:
            _write_json(os.path.join(record_dir, "result.json"), {
                "success": False, "error": "Cancelled",
                "duration_ms": int((time.monotonic() - start_time) * 1000),
                "session_id": result_thread_id,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
        raise
    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.error(f"Codex SDK error: {e}")
        return finalize(CLIResult(
            success=False, error=str(e),
            duration_ms=duration_ms, session_id=result_thread_id,
        ))


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
