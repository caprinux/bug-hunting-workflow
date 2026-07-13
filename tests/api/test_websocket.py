"""Tier 2 — WebSocket subscription + broadcast.

Uses Starlette's TestClient (sync, with a blocking portal) so we can hold a
websocket open and drive the async event_manager from the server side.

The app's startup enables a password, so we set a known one via env and connect
with a real session token — this also exercises the websocket auth path.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from bug_hunter.core.auth import issue_session_token
from bug_hunter.core.events import event_manager


@pytest.fixture
def ws_app(monkeypatch, db, temp_config):
    """The app wired to a temp DB/output dir with a known password so startup is
    hermetic (no random password, no .credentials written to the repo)."""
    import bug_hunter.main as main
    monkeypatch.setenv("BHW_PASSWORD", "testpw")
    monkeypatch.setattr(main, "load_config", lambda *a, **k: temp_config)
    return main.app


def test_ws_connects_with_token_and_receives_engagement_broadcast(ws_app):
    with TestClient(ws_app) as client:
        token = issue_session_token()  # signed with the active password ("testpw")
        with client.websocket_connect(f"/ws?engagement_id=eng-1&token={token}") as ws:
            client.portal.call(
                event_manager.emit, "log", "eng-1", "", "bug_hunter", {"message": "hello"}
            )
            msg = ws.receive_json()

    assert msg["type"] == "log"
    assert msg["engagement_id"] == "eng-1"
    assert msg["data"]["message"] == "hello"


def test_ws_rejects_bad_token(ws_app):
    with TestClient(ws_app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws?engagement_id=eng-1&token=bogus"):
                pass


def test_ws_scoped_connection_ignores_other_engagements(ws_app):
    with TestClient(ws_app) as client:
        token = issue_session_token()
        with client.websocket_connect(f"/ws?engagement_id=mine&token={token}") as ws:
            # Event for a different engagement must not arrive here…
            client.portal.call(
                event_manager.emit, "log", "someone-else", "", "s", {"message": "nope"}
            )
            # …but one for ours does. Messages are ordered per connection, so
            # receiving "yes" proves "nope" was filtered out.
            client.portal.call(
                event_manager.emit, "log", "mine", "", "s", {"message": "yes"}
            )
            msg = ws.receive_json()

    assert msg["data"]["message"] == "yes"
