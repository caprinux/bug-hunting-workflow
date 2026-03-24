"""Authentication helpers for API and WebSocket access."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

_AUTH_PASSWORD = os.environ.get("BHW_PASSWORD", "")
_SESSION_TTL_SECONDS = int(os.environ.get("BHW_SESSION_TTL", "43200"))


def set_auth_password(password: str) -> None:
    """Update the active authentication password used for session signing."""
    global _AUTH_PASSWORD
    _AUTH_PASSWORD = password or ""


def get_auth_password() -> str:
    return _AUTH_PASSWORD


def verify_password(password: str) -> bool:
    if not _AUTH_PASSWORD:
        return True
    return secrets.compare_digest(password.encode(), _AUTH_PASSWORD.encode())


def issue_session_token(ttl_seconds: int | None = None) -> str:
    """Issue a signed bearer token for API/WebSocket auth."""
    ttl = ttl_seconds or _SESSION_TTL_SECONDS
    expiry = int(time.time()) + ttl
    nonce = secrets.token_urlsafe(18).rstrip("=")
    payload = f"{expiry}.{nonce}"
    signature = _sign_payload(payload)
    return f"{payload}.{signature}"


def verify_session_token(token: str) -> bool:
    """Validate a signed bearer token."""
    if not token or not _AUTH_PASSWORD:
        return False

    try:
        expiry_str, nonce, signature = token.split(".", 2)
        expiry = int(expiry_str)
    except ValueError:
        return False

    if expiry < int(time.time()):
        return False

    payload = f"{expiry}.{nonce}"
    expected = _sign_payload(payload)
    return secrets.compare_digest(signature, expected)


def _sign_payload(payload: str) -> str:
    digest = hmac.new(
        _AUTH_PASSWORD.encode(),
        payload.encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")
