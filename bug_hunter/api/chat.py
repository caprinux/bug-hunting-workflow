"""Chat API — per-engagement conversational interface with Claude."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from bug_hunter.core.cli_wrapper import run_claude_chat, StreamEvent
from bug_hunter.core.database import (
    create_chat, get_chat, list_chats, update_chat, delete_chat,
    create_chat_message, list_chat_messages,
    get_engagement, list_runs,
)
from bug_hunter.core.events import event_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/engagements/{engagement_id}/chats", tags=["chat"])

# Track active streaming tasks to prevent concurrent responses on same chat
_active_chats: dict[str, asyncio.Task] = {}


# --- Request models ---

class CreateChatRequest(BaseModel):
    title: str = "New Chat"


class SendMessageRequest(BaseModel):
    content: str


class UpdateChatRequest(BaseModel):
    title: str


# --- Context builder ---

def _get_chat_workspace(engagement_id: str) -> str:
    """Get the shared chat workspace directory for an engagement."""
    eng = get_engagement(engagement_id)
    cfg = eng.get("config", {}) if eng else {}
    output_dir = cfg.get("pipeline", {}).get("output_dir", "./audit_output")
    workspace = os.path.join(os.path.abspath(output_dir), "engagements", engagement_id, "chat_workspace")
    os.makedirs(workspace, exist_ok=True)
    return workspace


def _build_chat_context(engagement_id: str) -> str:
    """Build a system prompt pointing Claude at engagement files."""
    eng = get_engagement(engagement_id)
    if not eng:
        return ""

    cfg = eng.get("config", {})
    eng_cfg = cfg.get("engagement", {})
    output_dir = cfg.get("pipeline", {}).get("output_dir", "./audit_output")

    parts = []
    parts.append(f"# Engagement: {eng['name']}")
    parts.append(f"Type: {eng['type']}")

    # Write scope/infra to a file so it doesn't blow up the system prompt
    eng_dir = os.path.join(os.path.abspath(output_dir), "engagements", engagement_id)
    os.makedirs(eng_dir, exist_ok=True)
    context_file = os.path.join(eng_dir, "chat_context.md")
    with open(context_file, "w") as f:
        if eng_cfg.get("scope_definition"):
            f.write(f"## Scope\n{eng_cfg['scope_definition']}\n\n")
        if eng_cfg.get("infra_config"):
            f.write(f"## Infrastructure\n{eng_cfg['infra_config']}\n")
    parts.append(f"\nEngagement details: Read {context_file}")
    cumulative_dir = os.path.join(eng_dir, "cumulative")

    file_refs = []
    for filename, label in [
        ("all_confirmed_bugs.json", "Confirmed bugs"),
        ("all_cannot_validate.json", "Cannot-validate bugs"),
        ("intelligence.json", "Informational findings"),
        ("report.md", "Summary report"),
    ]:
        path = os.path.join(cumulative_dir, filename)
        if os.path.exists(path) and os.path.getsize(path) > 2:
            file_refs.append(f"- {label}: {path}")

    # Find latest run's scope and BUGS.json
    runs = list_runs(engagement_id)
    if runs:
        latest = runs[-1]
        run_dir = os.path.join(eng_dir, "runs", latest["id"])
        scope_path = os.path.join(run_dir, "01_scoper", "scope.json")
        bugs_path = os.path.join(run_dir, "02_bug_hunter", "BUGS.json")
        if os.path.exists(scope_path):
            file_refs.append(f"- Scope/architecture: {scope_path}")
        if os.path.exists(bugs_path):
            file_refs.append(f"- All bugs (raw): {bugs_path}")

    # Shared resources directory
    chat_resources = os.path.join(os.path.abspath(output_dir), "chat_resources")
    if os.path.isdir(chat_resources) and os.listdir(chat_resources):
        file_refs.append(f"- Shared reference files: {chat_resources}")

    if file_refs:
        parts.append("\n## Data Files\nRead these files for detailed engagement data:")
        parts.extend(file_refs)

    parts.append(f"\n## Runs ({len(runs)} total)")
    for r in runs:
        parts.append(f"- Run #{r['run_number']} ({r['run_type']}) — {r['status']}")

    return "\n".join(parts)


# --- Endpoints ---

@router.get("")
async def api_list_chats(engagement_id: str):
    return list_chats(engagement_id)


@router.post("")
async def api_create_chat(engagement_id: str, body: CreateChatRequest):
    eng = get_engagement(engagement_id)
    if not eng:
        raise HTTPException(404, "Engagement not found")
    return create_chat(engagement_id, body.title)


@router.get("/{chat_id}")
async def api_get_chat(engagement_id: str, chat_id: str):
    chat = get_chat(chat_id)
    if not chat or chat["engagement_id"] != engagement_id:
        raise HTTPException(404, "Chat not found")
    messages = list_chat_messages(chat_id)
    return {**chat, "messages": messages}


@router.delete("/{chat_id}")
async def api_delete_chat(engagement_id: str, chat_id: str):
    chat = get_chat(chat_id)
    if not chat or chat["engagement_id"] != engagement_id:
        raise HTTPException(404, "Chat not found")
    # Cancel active streaming if any
    task = _active_chats.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
    delete_chat(chat_id)
    return {"status": "deleted"}


@router.patch("/{chat_id}")
async def api_update_chat(engagement_id: str, chat_id: str, body: UpdateChatRequest):
    chat = get_chat(chat_id)
    if not chat or chat["engagement_id"] != engagement_id:
        raise HTTPException(404, "Chat not found")
    return update_chat(chat_id, title=body.title)


@router.post("/{chat_id}/messages")
async def api_send_message(engagement_id: str, chat_id: str, body: SendMessageRequest):
    chat = get_chat(chat_id)
    if not chat or chat["engagement_id"] != engagement_id:
        raise HTTPException(404, "Chat not found")

    # Prevent concurrent responses
    existing = _active_chats.get(chat_id)
    if existing and not existing.done():
        raise HTTPException(409, "A response is already streaming for this chat")

    # Store user message
    user_msg = create_chat_message(chat_id, "user", body.content)

    # Determine if this is a new session or a resume
    session_id = chat.get("claude_session_id")
    is_resume = bool(session_id)
    if not session_id:
        session_id = str(uuid4())
        update_chat(chat_id, claude_session_id=session_id)

    # Build context for first message only
    system_prompt = ""
    if not is_resume:
        system_prompt = _build_chat_context(engagement_id)

    # Auto-title from first user message
    if chat["title"] == "New Chat":
        title = body.content[:60].strip()
        if len(body.content) > 60:
            title += "..."
        update_chat(chat_id, title=title)

    # Launch streaming response in background
    workspace = _get_chat_workspace(engagement_id)
    task = asyncio.create_task(
        _stream_response(engagement_id, chat_id, body.content,
                         session_id, is_resume, system_prompt, workspace)
    )
    _active_chats[chat_id] = task

    return {"status": "streaming", "message_id": user_msg["id"]}


async def _stream_response(engagement_id: str, chat_id: str, prompt: str,
                            session_id: str, is_resume: bool, system_prompt: str,
                            workspace: str = ""):
    """Run Claude and stream tokens to WebSocket."""
    accumulated: list[str] = []

    def on_event(event: StreamEvent):
        if event.type == "assistant":
            raw = event.data
            text = ""
            # Handle content_block_delta with text_delta
            if raw.get("type") == "content_block_delta":
                delta = raw.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
            # Handle full message content (non-streaming fallback)
            elif raw.get("type") == "message":
                content = raw.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text" and block.get("text"):
                            text += block["text"]
            if text:
                accumulated.append(text)
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        event_manager.emit_chat_stream(engagement_id, chat_id, text)
                    )
                except RuntimeError:
                    pass

    try:
        # Build additional dirs for engagement data access
        eng = get_engagement(engagement_id)
        cfg = eng.get("config", {}) if eng else {}
        output_dir = cfg.get("pipeline", {}).get("output_dir", "./audit_output")
        eng_dir = os.path.join(os.path.abspath(output_dir), "engagements", engagement_id)
        extra_dirs = [eng_dir] if os.path.isdir(eng_dir) else []
        chat_resources = os.path.join(os.path.abspath(output_dir), "chat_resources")
        if os.path.isdir(chat_resources):
            extra_dirs.append(chat_resources)

        result = await run_claude_chat(
            prompt=prompt,
            session_id=session_id,
            is_resume=is_resume,
            system_prompt=system_prompt,
            model="sonnet",
            cwd=workspace or None,
            additional_dirs=extra_dirs,
            timeout=600,
            on_event=on_event,
        )

        # Extract full response text
        full_text = "".join(accumulated)
        if not full_text and result.result:
            # Fallback: use the parsed result
            if isinstance(result.result, str):
                full_text = result.result
            elif isinstance(result.result, dict):
                full_text = result.result.get("text", json.dumps(result.result))

        if not full_text:
            full_text = result.error or "No response received."

        # Store assistant message
        msg = create_chat_message(chat_id, "assistant", full_text)

        await event_manager.emit_chat_complete(engagement_id, chat_id, msg["id"])

    except asyncio.CancelledError:
        logger.info(f"Chat response cancelled for chat {chat_id}")
    except Exception as e:
        logger.error(f"Chat response error for chat {chat_id}: {e}")
        await event_manager.emit_chat_error(engagement_id, chat_id, str(e))
    finally:
        _active_chats.pop(chat_id, None)


# --- Workspace file endpoints ---

@router.get("/files")
async def api_list_chat_files(engagement_id: str, path: str = Query(default="")):
    """List files in the engagement's chat workspace."""
    workspace = _get_chat_workspace(engagement_id)
    target = os.path.realpath(os.path.join(workspace, path))
    ws_real = os.path.realpath(workspace)
    try:
        if os.path.commonpath([ws_real, target]) != ws_real:
            raise HTTPException(403, "Path traversal detected")
    except ValueError:
        raise HTTPException(403, "Path traversal detected")
    if not os.path.exists(target):
        return {"files": []}
    if os.path.isfile(target):
        return {"files": [{"name": os.path.basename(target), "path": path, "is_dir": False,
                           "size": os.path.getsize(target)}]}
    files = []
    for entry in sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name)):
        rel = os.path.relpath(entry.path, workspace)
        item = {"name": entry.name, "path": rel, "is_dir": entry.is_dir()}
        if entry.is_file():
            item["size"] = entry.stat().st_size
        files.append(item)
    return {"files": files}


@router.get("/files/download")
async def api_download_chat_file(engagement_id: str, path: str = Query(...)):
    """Download a file from the chat workspace."""
    workspace = _get_chat_workspace(engagement_id)
    filepath = os.path.realpath(os.path.join(workspace, path))
    ws_real = os.path.realpath(workspace)
    if not filepath.startswith(ws_real):
        raise HTTPException(403, "Path traversal detected")
    if not os.path.isfile(filepath):
        raise HTTPException(404, "File not found")
    return FileResponse(filepath, filename=os.path.basename(filepath))
