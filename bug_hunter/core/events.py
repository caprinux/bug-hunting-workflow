"""WebSocket event manager for real-time pipeline updates."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class EventManager:
    """Manages WebSocket connections and broadcasts pipeline events."""

    def __init__(self):
        self._connections: dict[str, set[WebSocket]] = {}  # engagement_id -> websockets
        self._global_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, engagement_id: str = None,
                      subprotocol: str | None = None):
        await websocket.accept(subprotocol=subprotocol)
        async with self._lock:
            if engagement_id:
                if engagement_id not in self._connections:
                    self._connections[engagement_id] = set()
                self._connections[engagement_id].add(websocket)
            else:
                self._global_connections.add(websocket)
        logger.info(f"WebSocket connected (engagement: {engagement_id or 'global'})")

    async def disconnect(self, websocket: WebSocket, engagement_id: str = None):
        async with self._lock:
            if engagement_id and engagement_id in self._connections:
                self._connections[engagement_id].discard(websocket)
                if not self._connections[engagement_id]:
                    del self._connections[engagement_id]
            else:
                self._global_connections.discard(websocket)
        logger.info(f"WebSocket disconnected (engagement: {engagement_id or 'global'})")

    async def emit(self, event_type: str, engagement_id: str, run_id: str = "",
                   stage: str = "", data: dict[str, Any] = None):
        """Broadcast an event to relevant WebSocket connections and persist to DB."""
        timestamp = datetime.now(timezone.utc).isoformat()
        message = {
            "type": event_type,
            "engagement_id": engagement_id,
            "run_id": run_id,
            "stage": stage,
            "data": data or {},
            "timestamp": timestamp,
        }
        msg_json = json.dumps(message)

        # Persist to database (skip high-frequency stream events to avoid flooding the DB)
        if run_id and event_type not in ("agent_stream", "chat_stream"):
            try:
                from bug_hunter.core.database import create_event
                create_event(engagement_id, run_id, event_type, stage, data, timestamp)
            except Exception as e:
                logger.warning(f"Failed to persist event: {e}")

        targets: set[WebSocket] = set()
        async with self._lock:
            targets.update(self._global_connections)
            if engagement_id in self._connections:
                targets.update(self._connections[engagement_id])

        disconnected: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(msg_json)
            except Exception:
                disconnected.append(ws)

        if disconnected:
            async with self._lock:
                for ws in disconnected:
                    self._global_connections.discard(ws)
                    for conns in self._connections.values():
                        conns.discard(ws)

    async def emit_stage_update(self, engagement_id: str, run_id: str, stage: str,
                                status: str, **extra):
        await self.emit("stage_update", engagement_id, run_id, stage,
                        {"status": status, **extra})

    async def emit_progress(self, engagement_id: str, run_id: str, stage: str,
                            current: int, total: int, message: str = ""):
        await self.emit("progress", engagement_id, run_id, stage,
                        {"current": current, "total": total, "message": message})

    async def emit_log(self, engagement_id: str, run_id: str, stage: str, message: str):
        await self.emit("log", engagement_id, run_id, stage, {"message": message})

    async def emit_error(self, engagement_id: str, run_id: str, stage: str, error: str):
        await self.emit("error", engagement_id, run_id, stage, {"error": error})

    async def emit_completion(self, engagement_id: str, run_id: str, summary: dict):
        await self.emit("completion", engagement_id, run_id, data=summary)

    async def emit_agent_stream(self, engagement_id: str, run_id: str, stage: str,
                                agent_id: str, text: str):
        await self.emit("agent_stream", engagement_id, run_id, stage,
                        {"agent_id": agent_id, "text": text})

    async def emit_chat_stream(self, engagement_id: str, chat_id: str, text: str):
        await self.emit("chat_stream", engagement_id, data={
            "chat_id": chat_id, "text": text,
        })

    async def emit_chat_complete(self, engagement_id: str, chat_id: str,
                                  message_id: str):
        await self.emit("chat_complete", engagement_id, data={
            "chat_id": chat_id, "message_id": message_id,
        })

    async def emit_chat_error(self, engagement_id: str, chat_id: str, error: str):
        await self.emit("chat_error", engagement_id, data={
            "chat_id": chat_id, "error": error,
        })


event_manager = EventManager()
