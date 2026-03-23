"""WebSocket endpoint for real-time pipeline updates."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from bug_hunter.core.events import event_manager

ws_router = APIRouter()


@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, engagement_id: str = Query(default=None)):
    """WebSocket connection for real-time updates.

    Connect to /ws for global updates, or /ws?engagement_id=xxx for engagement-specific updates.
    """
    await event_manager.connect(websocket, engagement_id)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        await event_manager.disconnect(websocket, engagement_id)
    except Exception:
        await event_manager.disconnect(websocket, engagement_id)
