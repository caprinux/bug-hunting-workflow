"""WebSocket endpoint for real-time pipeline updates."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status

from bug_hunter.core.events import event_manager

ws_router = APIRouter()


@ws_router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    engagement_id: str = Query(default=None),
    token: str = Query(default=""),
):
    """WebSocket connection for real-time updates.

    Connect to /ws?token=<password> for global updates,
    or /ws?token=<password>&engagement_id=xxx for engagement-specific updates.
    """
    from bug_hunter.main import AUTH_PASSWORD

    if AUTH_PASSWORD and not secrets.compare_digest(token.encode(), AUTH_PASSWORD.encode()):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
        return

    await event_manager.connect(websocket, engagement_id)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        await event_manager.disconnect(websocket, engagement_id)
    except Exception:
        await event_manager.disconnect(websocket, engagement_id)
