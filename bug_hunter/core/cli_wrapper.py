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
        }
        if cwd:
            opts_kwargs["cwd"] = cwd
        if session_id and is_resume:
            opts_kwargs["continue_conversation"] = True
            opts_kwargs["session_id"] = session_id
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
            opts_kwargs["output_format"] = {"type": "json_schema", "json_schema": schema}

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
                raw_dict = {"type": type(msg).__name__}

                if isinstance(msg, AssistantMessage):
                    raw_dict["content"] = []
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            accumulated_text.append(block.text)
                            raw_dict["content"].append({"type": "text", "text": block.text})
                            if on_event:
                                on_event(StreamEvent(type="assistant", data={
                                    "type": "assistant",
                                    "message": {"content": [{"type": "text", "text": block.text}]},
                                }))
                        elif isinstance(block, ThinkingBlock):
                            raw_dict["content"].append({"type": "thinking", "thinking": block.thinking})
                            if on_event:
                                on_event(StreamEvent(type="assistant", data={
                                    "type": "content_block_delta",
                                    "delta": {"type": "thinking_delta", "thinking": block.thinking},
                                }))
                        elif isinstance(block, ToolUseBlock):
                            raw_dict["content"].append({"type": "tool_use", "name": block.name, "input": block.input})
                            if on_event:
                                on_event(StreamEvent(type="tool_use", data={
                                    "type": "tool_use", "name": block.name, "input": block.input,
                                }))

                elif isinstance(msg, UserMessage):
                    # Tool results
                    raw_dict["content"] = []
                    for block in msg.content:
                        if isinstance(block, ToolResultBlock):
                            raw_dict["content"].append({"type": "tool_result", "content": str(block.content)[:500]})

                elif isinstance(msg, ResultMessage):
                    cost_usd = getattr(msg, "total_cost_usd", 0.0) or 0.0
                    result_session_id = getattr(msg, "session_id", session_id) or session_id or ""
                    structured_output = getattr(msg, "structured_output", None)
                    usage = getattr(msg, "usage", None)
                    if isinstance(usage, dict):
                        pass
                    elif usage:
                        usage = vars(usage) if hasattr(usage, "__dict__") else None
                    raw_dict["cost_usd"] = cost_usd
                    raw_dict["session_id"] = result_session_id
                    if on_event:
                        on_event(StreamEvent(type="result", data=raw_dict))
                    break

                raw_str = json.dumps(raw_dict, default=str)
                raw_lines.append(raw_str)
                if record_dir:
                    _append_jsonl(os.path.join(record_dir, "stream.jsonl"), {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "stream": "sdk",
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
    """Run Claude Code for chat with session persistence.

    Uses the SDK's system_prompt parameter directly for the first message,
    and resumes with session_id for subsequent messages.
    """
    # Write system prompt to a temp file so run_claude can use it as agent_file
    tmp_file = None
    if system_prompt and not is_resume:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
        tmp.write(system_prompt)
        tmp.close()
        tmp_file = tmp.name

    try:
        return await run_claude(
            prompt=prompt,
            agent_file=tmp_file,
            model=model,
            cwd=cwd,
            timeout=timeout,
            on_event=on_event,
            additional_dirs=additional_dirs,
            session_id=session_id,
            is_resume=is_resume,
        )
    finally:
        if tmp_file:
            try:
                os.unlink(tmp_file)
            except OSError:
                pass


# --- Codex (JSON-RPC via app-server) ---

_codex_server: Optional[asyncio.subprocess.Process] = None
_codex_request_counter = 0
_codex_lock = asyncio.Lock()
_codex_run_lock = asyncio.Lock()  # Serialize all codex calls (single stdout reader)
_codex_initialized = False


async def _ensure_codex_server():
    """Start and initialize the codex app-server if not running."""
    global _codex_server, _codex_initialized, _codex_request_counter

    async with _codex_lock:
        if _codex_server and _codex_server.returncode is None and _codex_initialized:
            return
        _codex_initialized = False
        _codex_request_counter = 0

        _codex_server = await asyncio.create_subprocess_exec(
            "codex", "app-server", "--listen", "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        rid = await _codex_send("initialize", {
            "clientInfo": {"name": "bhw", "version": "1.0.0"},
            "capabilities": {"experimentalApi": True},
        })
        await _codex_read_response(rid, timeout=60)

        _codex_server.stdin.write((json.dumps({"method": "initialized"}) + "\n").encode())
        await _codex_server.stdin.drain()
        _codex_initialized = True
        logger.info("Codex app-server initialized")


async def _codex_send(method: str, params: dict) -> int:
    global _codex_request_counter
    _codex_request_counter += 1
    rid = _codex_request_counter
    msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    _codex_server.stdin.write((json.dumps(msg) + "\n").encode())
    await _codex_server.stdin.drain()
    return rid


async def _codex_respond(request_id, result: dict):
    msg = {"jsonrpc": "2.0", "id": request_id, "result": result}
    _codex_server.stdin.write((json.dumps(msg) + "\n").encode())
    await _codex_server.stdin.drain()


async def _codex_read_response(expected_id: int, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        line = await asyncio.wait_for(_codex_server.stdout.readline(), timeout=remaining)
        if not line:
            raise RuntimeError("Codex server closed")
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == expected_id:
            if "error" in msg and "result" not in msg:
                raise RuntimeError(f"Codex error: {msg['error']}")
            return msg.get("result", {})
        if "id" in msg and "method" in msg:
            await _codex_respond(msg["id"], {"accept": True})
    raise TimeoutError(f"Codex response timed out after {timeout}s")


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
    """Run Codex via the app-server JSON-RPC protocol. Supports session resume via thread_id."""
    async with _codex_run_lock:
        return await _run_codex_impl(
            prompt, model, cwd, timeout, on_event, additional_dirs,
            record_dir, record_metadata, output_schema_file, thread_id,
        )


async def _run_codex_impl(
    prompt, model, cwd, timeout, on_event, additional_dirs,
    record_dir, record_metadata, output_schema_file, thread_id,
) -> CLIResult:
    start_time = time.monotonic()
    raw_lines: list[str] = []
    cost_usd = 0.0
    result_thread_id = thread_id or ""
    error_msg = ""
    codex_messages: list[str] = []

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
        await _ensure_codex_server()

        if thread_id:
            result_thread_id = thread_id
        else:
            thread_params = {
                "model": model,
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
            }
            if cwd:
                thread_params["cwd"] = cwd
            rid = await _codex_send("thread/start", thread_params)
            thread_result = await _codex_read_response(rid, timeout=60)
            result_thread_id = thread_result.get("thread", {}).get("id", "")

        if not result_thread_id:
            return finalize(CLIResult(
                success=False, error="Failed to get thread ID",
                duration_ms=int((time.monotonic() - start_time) * 1000),
            ))

        # Send turn
        turn_params = {
            "threadId": result_thread_id,
            "input": [{"type": "text", "text": prompt}],
        }
        if output_schema_file:
            try:
                with open(output_schema_file) as f:
                    turn_params["outputSchema"] = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load output schema: {e}")

        rid = await _codex_send("turn/start", turn_params)
        turn_result = await _codex_read_response(rid, timeout=30)
        current_turn_id = turn_result.get("turn", {}).get("id", "")

        # Read events until turn completes
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                line = await asyncio.wait_for(_codex_server.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                error_msg = f"Timed out after {timeout}s"
                break
            if not line:
                error_msg = "Codex server closed unexpectedly"
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Server requests — handle by type
            if "id" in msg and "method" in msg:
                server_method = msg["method"]
                if server_method == "item/tool/call":
                    # Dynamic tool call — not supported, reject
                    await _codex_respond(msg["id"], {
                        "contentItems": [{"type": "text", "text": "Tool not available"}],
                        "success": False,
                    })
                else:
                    # Approval requests, permissions, etc. — auto-approve
                    await _codex_respond(msg["id"], {"accept": True})
                continue
            if "id" in msg and "result" in msg:
                continue

            method = msg.get("method", "")
            params = msg.get("params", {})

            # Filter by threadId and turnId — ignore stale events
            evt_thread = params.get("threadId", "")
            evt_turn = params.get("turnId", params.get("turn", {}).get("id", "") if isinstance(params.get("turn"), dict) else "")
            if evt_thread and evt_thread != result_thread_id:
                continue
            if current_turn_id and evt_turn and evt_turn != current_turn_id:
                continue

            raw_str = json.dumps(msg, default=str)
            raw_lines.append(raw_str)
            if record_dir:
                _append_jsonl(os.path.join(record_dir, "stream.jsonl"), {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "stream": "sdk", "raw": raw_str,
                })

            if method == "item/completed":
                item = params.get("item", {})
                item_type = item.get("type", "")
                if item_type == "agentMessage":
                    codex_messages.append(item.get("text", ""))
                    if on_event:
                        on_event(StreamEvent(type="assistant", data={
                            "type": "assistant",
                            "message": {"content": [{"type": "text", "text": item.get("text", "")}]},
                        }))
                elif item_type == "commandExecution":
                    if on_event:
                        on_event(StreamEvent(type="tool_use", data={
                            "type": "tool_use", "name": "Bash",
                            "input": {"command": item.get("command", "")},
                            "output": item.get("aggregatedOutput", "")[:500],
                        }))
                elif item_type == "reasoning":
                    if on_event:
                        on_event(StreamEvent(type="assistant", data={
                            "type": "content_block_delta",
                            "delta": {"type": "thinking_delta", "thinking": item.get("content", "")},
                        }))
            elif method == "turn/completed":
                # Check for failure via turn status
                turn = params.get("turn", {})
                if turn.get("status") == "failed":
                    error_msg = turn.get("error", {}).get("message", "Turn failed")
                elif on_event:
                    on_event(StreamEvent(type="result", data=params))
                break
            elif method == "thread/status/changed":
                status_type = params.get("status", {}).get("type", "")
                if status_type == "idle":
                    if on_event:
                        on_event(StreamEvent(type="result", data=params))
                    break
            elif method == "error":
                # Error notification — check if retryable
                if params.get("willRetry"):
                    continue  # Server will retry, keep waiting
                err_obj = params.get("error", {})
                error_msg = err_obj.get("message", "Unknown error") if isinstance(err_obj, dict) else str(err_obj)
                break
            elif method == "turn/failed":
                error_msg = params.get("error", {}).get("message", "Turn failed")
                break

        duration_ms = int((time.monotonic() - start_time) * 1000)

        if error_msg:
            return finalize(CLIResult(
                success=False, error=error_msg,
                duration_ms=duration_ms, cost_usd=cost_usd,
                session_id=result_thread_id,
            ))

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
            duration_ms=duration_ms, cost_usd=cost_usd,
            session_id=result_thread_id,
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
            duration_ms=duration_ms, cost_usd=cost_usd,
            session_id=result_thread_id,
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
