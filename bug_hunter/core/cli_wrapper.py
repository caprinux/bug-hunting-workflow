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
    reasoning_effort: Optional[str] = "xhigh",
    reasoning_summary: Optional[str] = "auto",
) -> CLIResult:
    """Run Codex via the codex-agent-sdk. Each call spawns a subprocess — safe for concurrency."""
    from codex_agent_sdk import (
        CodexSDKClient, CodexAgentOptions, SandboxMode, ApprovalPolicy,
        ReasoningEffort,
        ItemCompletedEvent, ItemStartedEvent,
        TurnCompletedEvent, TurnFailedEvent,
        ThreadStartedEvent, StreamErrorEvent,
        AgentMessageItem, CommandExecutionItem, ReasoningItem,
    )

    # Append the codex-only streaming guidance so the model knows to emit
    # narrative + delta arrays. Claude never sees this because run_claude is
    # a separate dispatch path.
    if developer_instructions is None:
        developer_instructions = CODEX_STREAMING_GUIDANCE.lstrip()
    elif "## Conversation streaming" not in developer_instructions:
        developer_instructions = developer_instructions.rstrip() + CODEX_STREAMING_GUIDANCE

    re_enum: Optional[ReasoningEffort] = None
    if reasoning_effort:
        try:
            re_enum = ReasoningEffort(reasoning_effort.lower())
        except ValueError:
            logger.warning(f"Unknown reasoning_effort {reasoning_effort!r}; using SDK default")

    # codex_agent_sdk doesn't expose reasoning_summary directly, so we plumb
    # it through config_overrides — the SDK forwards each entry to codex CLI
    # as `--config k=v`. The value must already be a TOML literal (a quoted
    # string), matching how the SDK encodes reasoning_effort itself.
    extra_overrides: dict[str, str] = {}
    if reasoning_summary:
        extra_overrides["model_reasoning_summary"] = f'"{reasoning_summary.lower()}"'

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
            reasoning_effort=re_enum,
            config_overrides=extra_overrides,
        )

        client = CodexSDKClient(opts)

        def _serialize_event(event) -> dict:
            """Produce a parseable dict for stream.jsonl — must round-trip the
            fields the /stages/{stage}/stream parser keys on."""
            payload: dict = {"type": "codex_event", "event_class": type(event).__name__}
            if isinstance(event, ThreadStartedEvent):
                payload.update({"event_type": "thread_started",
                                "thread_id": getattr(event, "thread_id", "")})
            elif isinstance(event, (ItemCompletedEvent, ItemStartedEvent)):
                item = event.item
                item_payload: dict = {"item_class": type(item).__name__}
                if isinstance(item, AgentMessageItem):
                    item_payload.update({"item_type": "agent_message",
                                         "text": item.text})
                elif isinstance(item, CommandExecutionItem):
                    item_payload.update({"item_type": "command_execution",
                                         "command": item.command,
                                         "output": (item.aggregated_output or "")[:1000]})
                elif isinstance(item, ReasoningItem):
                    item_payload.update({"item_type": "reasoning",
                                         "text": getattr(item, "text", "")})
                else:
                    cls_name = type(item).__name__
                    item_type = getattr(item, "type", "") or cls_name
                    item_payload["item_type"] = item_type
                    if cls_name == "WebSearchItem":
                        item_payload["query"] = getattr(item, "query", "") or ""
                    elif cls_name == "ErrorItem":
                        item_payload["message"] = getattr(item, "message", "") or ""
                    elif cls_name == "TodoListItem":
                        items = getattr(item, "items", None) or []
                        item_payload["items"] = [
                            {"text": getattr(t, "text", ""),
                             "completed": bool(getattr(t, "completed", False))}
                            for t in items
                        ]
                    elif cls_name == "FileChangeItem":
                        changes = getattr(item, "changes", None) or []
                        item_payload["changes"] = [
                            {"path": getattr(c, "path", ""),
                             "kind": getattr(getattr(c, "kind", None), "value",
                                             str(getattr(c, "kind", "")))}
                            for c in changes
                        ]
                    else:
                        item_payload["repr"] = str(item)[:500]
                event_type = ("item_completed" if isinstance(event, ItemCompletedEvent)
                              else "item_started")
                payload.update({"event_type": event_type, **item_payload})
            elif isinstance(event, TurnCompletedEvent):
                payload["event_type"] = "turn_completed"
                if getattr(event, "usage", None):
                    payload["usage"] = {
                        "input_tokens": event.usage.input_tokens,
                        "output_tokens": event.usage.output_tokens,
                        "cached_input_tokens": event.usage.cached_input_tokens,
                    }
            elif isinstance(event, TurnFailedEvent):
                payload.update({"event_type": "turn_failed",
                                "message": getattr(getattr(event, "error", None), "message", "")})
            elif isinstance(event, StreamErrorEvent):
                payload.update({"event_type": "stream_error",
                                "message": getattr(event, "message", "")})
            else:
                payload.update({"event_type": "other", "repr": str(event)[:500]})
            return payload

        async def _stream():
            nonlocal result_thread_id, usage_dict
            async for event in client.run_streamed(prompt, resume_thread_id=thread_id):
                raw_str = json.dumps(_serialize_event(event), default=str)
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
                            narrative, data_preview = split_codex_agent_message(item.text)
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
