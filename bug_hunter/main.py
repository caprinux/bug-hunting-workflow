"""Main entry point — starts the FastAPI server."""

from __future__ import annotations

import logging
import os
import secrets
import sys
from base64 import b64decode

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from bug_hunter.api.routes import router
from bug_hunter.api.platforms import router as platforms_router
from bug_hunter.api.websocket import ws_router
from bug_hunter.core.auth import get_auth_password, set_auth_password, verify_password, verify_session_token
from bug_hunter.core.config import load_config
from bug_hunter.core.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Bug Hunting Workflow", version="0.1.0")

_cors_origins = os.environ.get("BHW_CORS_ORIGINS", "").split(",")
_cors_origins = [o.strip() for o in _cors_origins if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["http://localhost", "http://127.0.0.1",
                                     "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBasic()

AUTH_PASSWORD = ""


def _resolve_auth_password() -> str:
    env_password = os.environ.get("BHW_PASSWORD", "").strip()
    if env_password:
        return env_password

    config_password = load_config().auth.password.strip()
    if config_password:
        return config_password

    return ""


AUTH_PASSWORD = _resolve_auth_password()
set_auth_password(AUTH_PASSWORD)

def verify_credentials(request: Request):
    auth_header = request.headers.get("authorization", "")
    active_password = get_auth_password()

    if not active_password:
        return True

    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if verify_session_token(token):
            return True
    elif auth_header.startswith("Basic "):
        encoded = auth_header[6:].strip()
        try:
            decoded = b64decode(encoded).decode("utf-8")
            _, password = decoded.split(":", 1)
        except Exception:
            password = ""
        if verify_password(password):
            return True

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Basic"},
    )


app.include_router(router, dependencies=[Depends(verify_credentials)])
app.include_router(platforms_router, dependencies=[Depends(verify_credentials)])
app.include_router(ws_router)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")


@app.on_event("startup")
async def startup():
    config = load_config()
    output_dir = config.pipeline.output_dir
    os.makedirs(output_dir, exist_ok=True)
    db_path = os.path.join(output_dir, "db.sqlite")
    init_db(db_path)
    logger.info(f"Database initialized at {db_path}")

    # Recover runs stuck in 'running' status from a previous server crash/restart
    _recover_interrupted_runs()

    global AUTH_PASSWORD
    AUTH_PASSWORD = _resolve_auth_password()
    if not AUTH_PASSWORD:
        # Check for persisted credentials file
        creds_file = os.path.join(os.path.dirname(__file__), "..", ".credentials")
        if os.path.exists(creds_file):
            with open(creds_file) as f:
                AUTH_PASSWORD = f.read().strip()
            logger.info("Loaded password from .credentials file")
        else:
            AUTH_PASSWORD = secrets.token_urlsafe(24)
            with open(creds_file, "w") as f:
                f.write(AUTH_PASSWORD)
            os.chmod(creds_file, 0o600)
            logger.info("Generated and saved password to .credentials file")
        print(f"\n{'='*60}", flush=True)
        print(f"  Authentication Password: {AUTH_PASSWORD}", flush=True)
        print(f"{'='*60}\n", flush=True)
    set_auth_password(AUTH_PASSWORD)


def _recover_interrupted_runs():
    """Mark runs stuck in 'running' as 'paused' so they can be resumed."""
    from bug_hunter.core.database import get_db
    try:
        with get_db() as conn:
            stuck = conn.execute(
                "SELECT id, engagement_id FROM runs WHERE status = 'running'"
            ).fetchall()
            for row in stuck:
                run_id, eng_id = row[0], row[1]
                conn.execute(
                    "UPDATE runs SET status = 'paused' WHERE id = ?", (run_id,)
                )
                logger.warning(f"Recovered interrupted run {run_id[:8]} (engagement {eng_id[:8]}) — marked as paused")
            if stuck:
                logger.info(f"Recovered {len(stuck)} interrupted run(s)")
    except Exception as e:
        logger.error(f"Failed to recover interrupted runs: {e}")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


if os.path.isdir(FRONTEND_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = os.path.join(FRONTEND_DIR, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        index_path = os.path.join(FRONTEND_DIR, "index.html")
        if os.path.isfile(index_path):
            return FileResponse(index_path)
        return HTMLResponse("<h1>Frontend not built. Run: cd frontend && npm run build</h1>")


def main():
    host = os.environ.get("BHW_HOST", "0.0.0.0")
    port = int(os.environ.get("BHW_PORT", "80"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
