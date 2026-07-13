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


# Appended to every codex developer_instructions. Tells the model how to use
# the schema's `narrative` field and that array data fields are deltas — the
# backend (run_codex._merge_codex_messages) unions them across emissions.
# Claude does NOT see this; its result-extraction takes only the last
# assistant message and would silently drop earlier deltas.
CODEX_STREAMING_GUIDANCE = """

## Conversation streaming

Your structured output is gated by a JSON schema. Every assistant message you emit must conform to it. The schema includes a `narrative` field alongside the data fields.

Use it so a human can follow what you are doing:

- **Always populate `narrative`** in every message with a 1-2 sentence plain-text note about what you are doing, what you just discovered, or what you plan to test next. Keep it short. No markdown.
- **Treat array data fields as deltas**: in each message include only items newly discovered or processed in THAT message. Do NOT re-emit items you already reported in a previous message — the system aggregates the union across all your messages.
- **For non-array fields** (scalars and objects): the FINAL message of the turn must contain the final, complete values. Earlier messages can leave them empty/placeholder; the system keeps the latest non-empty value seen.
- Empty arrays and empty narrative strings are fine when there is nothing new to say or to record.
"""


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
    reasoning_effort: Optional[str] = "xhigh",
    reasoning_summary: Optional[str] = "auto",
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
            reasoning_effort=reasoning_effort,
            reasoning_summary=reasoning_summary,
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


def split_codex_agent_message(text: str) -> tuple[Optional[str], Optional[str]]:
    """Split a codex AgentMessageItem text payload into the human-facing
    narrative and a compact preview of any non-empty data fields.

    The schema gates each codex turn into ``{"narrative": "...", "bugs": [...], ...}``.
    For the agent-stream sidebar we render the narrative as the visible
    bubble and only surface the data fields when at least one of them
    actually has content — empty arrays / strings / objects are hidden.

    Returns ``(narrative, data_preview)``. Either may be ``None``:
      - If ``text`` is not a JSON object with a ``narrative`` key,
        returns ``(text, None)`` so the caller falls back to raw text.
      - If the JSON has only the narrative or only empty data fields,
        the corresponding output is ``None``.
    """
    if not isinstance(text, str) or not text:
        return None, None
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text, None
    if not isinstance(obj, dict) or "narrative" not in obj:
        return text, None

    narrative = obj.get("narrative")
    narrative = narrative.strip() if isinstance(narrative, str) else ""
    filtered = {
        k: v for k, v in obj.items()
        if k != "narrative" and v not in ([], "", {}, None)
    }
    data_preview = json.dumps(filtered, default=str) if filtered else None
    return (narrative or None), data_preview


def _merge_codex_messages(messages: list[str]) -> Any:
    """Aggregate every parseable AgentMessageItem from a codex turn.

    With the schema gating every assistant message, agents emit incremental
    deltas across many turns. We merge them so callers see one unified
    structured result:
      - top-level array fields are concatenated in emit order;
      - top-level scalar/object fields take the latest non-empty value;
      - the human-facing `narrative` field is dropped from the merged
        result (it is a per-message conversation log, not a final field).

    If no message parses as a JSON object, fall back to the original
    "last message wins" + best-effort parse behavior.
    """
    if not messages:
        return None

    parsed_objects: list[dict] = []
    for msg in messages:
        try:
            obj = json.loads(msg)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            parsed_objects.append(obj)

    if not parsed_objects:
        last = messages[-1]
        try:
            return json.loads(last)
        except (json.JSONDecodeError, TypeError):
            return _parse_result_payload(last)

    merged: dict = {}
    for obj in parsed_objects:
        for k, v in obj.items():
            if k == "narrative":
                continue
            if isinstance(v, list):
                existing = merged.get(k)
                if isinstance(existing, list):
                    existing.extend(v)
                else:
                    merged[k] = list(v)
            else:
                # scalars/objects: latest non-empty wins
                if v in (None, "", {}):
                    merged.setdefault(k, v)
                else:
                    merged[k] = v
    return merged


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
    container: Optional["ContainerSpec"] = None,
) -> CLIResult:
    """Run Claude Code via the claude-agent-sdk.

    When ``container`` is given, the claude CLI runs inside a Docker container
    (isolated filesystem view) instead of directly on the host — see
    :mod:`bug_hunter.core.sandbox`.
    """
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
        if container is not None:
            # Run claude inside a Docker container: point cli_path at a wrapper
            # that execs `docker run ... /opt/claude "$@"`. The container's -w and
            # HOME are /work, and mounts are the only writable paths, so host
            # cwd/add_dirs are irrelevant here. Skip the SDK's pre-flight `-v`
            # probe (a second container spawn).
            from bug_hunter.core.sandbox import seed_agent_home, write_claude_wrapper
            seed_agent_home(container)
            wrapper_dir = record_dir or os.path.join(os.path.abspath(container.work_host), ".bhw")
            opts_kwargs["cli_path"] = write_claude_wrapper(container, wrapper_dir)
            os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = "1"
        elif cwd:
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

        if additional_dirs and container is None:
            # In container mode, host add_dirs are meaningless — the container's
            # mounts define what the agent can see.
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
                    structured_output = getattr(msg, "structured_output", None) or structured_output
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


# --- Codex (via the official OpenAI Codex Python SDK: openai_codex) ---

def _codex_reasoning_text(item: Any) -> str:
    """Flatten a ReasoningThreadItem's summary + content lists into one string."""
    parts: list[str] = []
    parts.extend(getattr(item, "summary", None) or [])
    parts.extend(getattr(item, "content", None) or [])
    return " ".join(p for p in parts if isinstance(p, str)).strip()


def _serialize_codex_item(item: Any, payload: dict) -> None:
    """Serialize a ThreadItem's ``root`` into the legacy codex_event schema.

    The keys here (``item_type`` ∈ agent_message/command_execution/reasoning
    and their text/command fields) are what the /stages/{stage}/stream replay
    endpoint parses, so they must stay stable across SDK migrations.
    """
    payload["item_class"] = type(item).__name__
    itype = getattr(item, "type", "") or ""
    if itype == "agentMessage":
        payload.update({"item_type": "agent_message", "text": getattr(item, "text", "") or ""})
    elif itype == "commandExecution":
        payload.update({"item_type": "command_execution",
                        "command": getattr(item, "command", "") or "",
                        "output": (getattr(item, "aggregated_output", "") or "")[:1000]})
    elif itype == "reasoning":
        payload.update({"item_type": "reasoning", "text": _codex_reasoning_text(item)})
    elif itype == "webSearch":
        payload.update({"item_type": "web_search", "query": getattr(item, "query", "") or ""})
    elif itype == "fileChange":
        changes = getattr(item, "changes", None) or []
        payload.update({"item_type": "file_change",
                        "changes": [{"path": getattr(c, "path", "")} for c in changes]})
    else:
        payload.update({"item_type": itype or type(item).__name__, "repr": str(item)[:500]})


def _serialize_codex_event(event: Any) -> dict:
    """Produce a parseable dict for stream.jsonl from an openai_codex Notification.

    Routes on the JSON-RPC ``method`` string and preserves the same
    ``type=codex_event`` shape the previous SDK emitted so the stream replay
    endpoint keeps working unchanged.
    """
    method = getattr(event, "method", "") or ""
    p = getattr(event, "payload", None)
    payload: dict = {"type": "codex_event", "method": method, "event_class": type(p).__name__}

    if method == "thread/started":
        payload.update({"event_type": "thread_started",
                        "thread_id": getattr(getattr(p, "thread", None), "id", "")})
    elif method in ("item/completed", "item/started"):
        payload["event_type"] = "item_completed" if method == "item/completed" else "item_started"
        item = getattr(getattr(p, "item", None), "root", None)
        _serialize_codex_item(item, payload)
    elif method == "turn/completed":
        turn = getattr(p, "turn", None)
        payload.update({"event_type": "turn_completed",
                        "status": getattr(getattr(turn, "status", None), "value", "")})
        err = getattr(turn, "error", None)
        if err is not None:
            payload["message"] = getattr(err, "message", "")
    elif method == "thread/tokenUsage/updated":
        total = getattr(getattr(p, "token_usage", None), "total", None)
        payload["event_type"] = "token_usage"
        if total is not None:
            payload["usage"] = {
                "input_tokens": getattr(total, "input_tokens", 0),
                "output_tokens": getattr(total, "output_tokens", 0),
                "cached_input_tokens": getattr(total, "cached_input_tokens", 0),
            }
    else:
        payload.update({"event_type": "other", "repr": str(p)[:500]})
    return payload


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
    reasoning_effort: Optional[str] = "xhigh",
    reasoning_summary: Optional[str] = "auto",
    container: Optional["ContainerSpec"] = None,
) -> CLIResult:
    """Run Codex via the official OpenAI Codex Python SDK (``openai_codex``).

    Each call opens its own ``AsyncCodex`` — a dedicated codex app-server
    subprocess — so concurrent calls are isolated and safe for parallel
    hunting. Passing ``thread_id`` resumes that thread instead of starting a
    fresh one; otherwise the thread is created ephemeral.

    When ``container`` is given, the codex app-server runs inside a Docker
    container (isolated filesystem view) — see :mod:`bug_hunter.core.sandbox`.
    """
    from openai_codex import AsyncCodex, ApprovalMode, CodexConfig, Sandbox

    # Append the codex-only streaming guidance so the model knows to emit
    # narrative + delta arrays. Claude never sees this because run_claude is
    # a separate dispatch path.
    if developer_instructions is None:
        developer_instructions = CODEX_STREAMING_GUIDANCE.lstrip()
    elif "## Conversation streaming" not in developer_instructions:
        developer_instructions = developer_instructions.rstrip() + CODEX_STREAMING_GUIDANCE

    # Reasoning knobs are forwarded to the codex app-server as config overrides,
    # matching the CLI's `model_reasoning_effort` / `model_reasoning_summary`
    # keys. The official SDK takes a plain JSON config object (no TOML quoting).
    thread_config: dict[str, Any] = {}
    if reasoning_effort:
        thread_config["model_reasoning_effort"] = reasoning_effort.lower()
    if reasoning_summary:
        thread_config["model_reasoning_summary"] = reasoning_summary.lower()

    # Structured output is passed to the turn as a schema dict, not a file path.
    output_schema: Optional[dict] = None
    if output_schema_file:
        with open(output_schema_file) as f:
            output_schema = json.load(f)

    start_time = time.monotonic()
    raw_lines: list[str] = []
    result_thread_id = thread_id or ""
    codex_messages: list[str] = []
    usage_dict: Optional[dict] = None

    if record_dir:
        os.makedirs(record_dir, exist_ok=True)
        _write_text(os.path.join(record_dir, "prompt.txt"), prompt)
        _write_json(os.path.join(record_dir, "request.json"), {
            "cli": "openai-codex", "model": model, "thread_id": thread_id or "",
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

    # In container mode the codex app-server runs inside Docker; point the SDK's
    # spawned process at `docker run …` and make the thread cwd the in-container
    # /work mount. full_access is safe because the container is the jail.
    codex_config = None
    codex_cwd = cwd
    if container is not None:
        from bug_hunter.core.sandbox import WORK, codex_launch_args, seed_agent_home
        seed_agent_home(container)
        codex_config = CodexConfig(launch_args_override=codex_launch_args(container))
        codex_cwd = WORK

    try:
        async def _run():
            nonlocal result_thread_id, usage_dict
            async with AsyncCodex(config=codex_config) as codex:
                # Full filesystem access + no approval prompts replace the old
                # SandboxMode.FULL_ACCESS / ApprovalPolicy.NEVER. Full access
                # subsumes the previous additional_writable_dirs.
                start_kwargs: dict[str, Any] = {
                    "model": model,
                    "cwd": codex_cwd,
                    "sandbox": Sandbox.full_access,
                    "approval_mode": ApprovalMode.deny_all,
                    "developer_instructions": developer_instructions,
                    "config": thread_config or None,
                }
                if thread_id:
                    thread = await codex.thread_resume(thread_id, **start_kwargs)
                else:
                    # Non-ephemeral so the thread's rollout is persisted to disk
                    # and can be resumed on a later run (see bug_hunter re-hunts).
                    thread = await codex.thread_start(ephemeral=False, **start_kwargs)
                result_thread_id = getattr(thread, "id", "") or result_thread_id

                turn = await thread.turn(prompt, output_schema=output_schema)
                async for event in turn.stream():
                    try:
                        serialized = _serialize_codex_event(event)
                    except Exception:
                        serialized = {"type": "codex_event", "event_type": "other"}
                    raw_str = json.dumps(serialized, default=str)
                    raw_lines.append(raw_str)
                    if record_dir:
                        _append_jsonl(os.path.join(record_dir, "stream.jsonl"), {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "stream": "sdk", "raw": raw_str,
                        })

                    method = getattr(event, "method", "") or ""
                    p = getattr(event, "payload", None)

                    if method == "thread/started":
                        tid = getattr(getattr(p, "thread", None), "id", "")
                        if tid:
                            result_thread_id = tid

                    elif method == "item/completed":
                        item = getattr(getattr(p, "item", None), "root", None)
                        itype = getattr(item, "type", "") or ""
                        if itype == "agentMessage":
                            text = getattr(item, "text", "") or ""
                            codex_messages.append(text)
                            if on_event:
                                narrative, data_preview = split_codex_agent_message(text)
                                if narrative:
                                    on_event(StreamEvent(type="assistant", data={
                                        "type": "assistant",
                                        "message": {"content": [{"type": "text", "text": narrative}]},
                                    }))
                                if data_preview:
                                    on_event(StreamEvent(type="assistant", data={
                                        "type": "assistant",
                                        "message": {"content": [{"type": "text", "text": data_preview}]},
                                    }))
                        elif itype == "commandExecution":
                            if on_event:
                                on_event(StreamEvent(type="tool_use", data={
                                    "type": "tool_use", "name": "Bash",
                                    "input": {"command": getattr(item, "command", "") or ""},
                                    "output": (getattr(item, "aggregated_output", "") or "")[:500],
                                }))
                        elif itype == "reasoning":
                            if on_event:
                                on_event(StreamEvent(type="assistant", data={
                                    "type": "content_block_delta",
                                    "delta": {"type": "thinking_delta",
                                              "thinking": _codex_reasoning_text(item)},
                                }))

                    elif method == "thread/tokenUsage/updated":
                        total = getattr(getattr(p, "token_usage", None), "total", None)
                        if total is not None:
                            usage_dict = {
                                "input_tokens": getattr(total, "input_tokens", 0),
                                "output_tokens": getattr(total, "output_tokens", 0),
                                "cached_input_tokens": getattr(total, "cached_input_tokens", 0),
                            }

                    elif method == "turn/completed":
                        turn_obj = getattr(p, "turn", None)
                        status = getattr(getattr(turn_obj, "status", None), "value", "")
                        if on_event:
                            on_event(StreamEvent(type="result", data={"type": "result"}))
                        if status == "failed":
                            err = getattr(turn_obj, "error", None)
                            raise RuntimeError(getattr(err, "message", "") or "codex turn failed")

        if timeout:
            await asyncio.wait_for(_run(), timeout=timeout)
        else:
            await _run()

        duration_ms = int((time.monotonic() - start_time) * 1000)

        parsed_result = _merge_codex_messages(codex_messages)

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
