"""Browser proxy service for security auditing behind anti-bot protection.

Runs camoufox on a residential IP to bypass Akamai/WAF and exposes an HTTP API
for the bug hunter agent to make requests through a real browser.

All browser operations run on a single dedicated thread to avoid playwright
thread-safety issues. Flask requests queue work to this thread and wait.

Usage:
    pip install flask camoufox
    python3 server.py [--port 8899]
"""

import argparse
import base64
import json
import logging
import queue
import time
import threading
from uuid import uuid4
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

SESSION_TIMEOUT = 600

# Single browser thread processes all playwright operations
_work_queue: queue.Queue = queue.Queue()
_browser_thread: threading.Thread = None


class BrowserSession:
    """A persistent camoufox browser session. Created and used only on the browser thread."""

    def __init__(self):
        from camoufox.sync_api import Camoufox
        self.id = str(uuid4())
        self._camoufox = Camoufox(headless=True, humanize=True)
        self._browser = self._camoufox.__enter__()
        self.page = self._browser.new_page()
        self.page.set_viewport_size({"width": 1920, "height": 1080})
        self.last_used = time.time()
        self.challenge_solved = False
        logger.info(f"Session {self.id[:8]} created")

    def close(self):
        try:
            self._camoufox.__exit__(None, None, None)
        except Exception:
            pass
        logger.info(f"Session {self.id[:8]} closed")

    def touch(self):
        self.last_used = time.time()

    def solve_challenge(self, warmup_url: str, wait: int = 5):
        if self.challenge_solved:
            return
        self.page.goto(warmup_url, timeout=30000)
        time.sleep(wait)
        self.challenge_solved = True
        self.touch()
        logger.info(f"Session {self.id[:8]} challenge solved via {warmup_url}")

    def get_cookies(self):
        return [
            {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"]}
            for c in self.page.context.cookies()
        ]


# Sessions dict — only accessed from the browser thread
_sessions: dict[str, BrowserSession] = {}


def _cleanup_sessions():
    now = time.time()
    expired = [sid for sid, s in _sessions.items() if now - s.last_used > SESSION_TIMEOUT]
    for sid in expired:
        _sessions[sid].close()
        del _sessions[sid]


def _get_or_create_session(session_id=None):
    if session_id and session_id in _sessions:
        s = _sessions[session_id]
        s.touch()
        return s
    s = BrowserSession()
    _sessions[s.id] = s
    return s


def _browser_worker():
    """Single thread that processes all browser operations."""
    logger.info("Browser worker thread started")
    while True:
        func, result_holder, event = _work_queue.get()
        try:
            result_holder["result"] = func()
        except Exception as e:
            result_holder["error"] = str(e)
        event.set()


def _run_on_browser_thread(func, timeout=120):
    """Queue a function to run on the browser thread and wait for the result."""
    result_holder = {}
    event = threading.Event()
    _work_queue.put((func, result_holder, event))
    event.wait(timeout=timeout)
    if not event.is_set():
        raise TimeoutError("Browser operation timed out")
    if "error" in result_holder:
        raise RuntimeError(result_holder["error"])
    return result_holder.get("result")


# --- API Endpoints ---

@app.route("/health", methods=["GET"])
def health():
    try:
        count = _run_on_browser_thread(lambda: len(_sessions), timeout=5)
    except Exception:
        count = -1
    return jsonify({"status": "ok", "active_sessions": count})


@app.route("/session", methods=["POST"])
def create_session():
    """Create a persistent browser session.

    Request: {"warmup_url": "https://...", "wait": 5}
    Returns: {"session_id": "...", "challenge_solved": true}
    """
    data = request.get_json() or {}
    warmup_url = data.get("warmup_url")
    wait = data.get("wait", 5)

    def work():
        _cleanup_sessions()
        session = _get_or_create_session()
        if warmup_url:
            session.solve_challenge(warmup_url, wait)
        return {"session_id": session.id, "challenge_solved": session.challenge_solved}

    try:
        return jsonify(_run_on_browser_thread(work))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/session/<session_id>", methods=["DELETE"])
def delete_session(session_id):
    """Close a browser session."""
    def work():
        session = _sessions.pop(session_id, None)
        if session:
            session.close()
            return {"status": "closed"}
        return None

    try:
        result = _run_on_browser_thread(work)
        if result:
            return jsonify(result)
        return jsonify({"error": "session not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/request", methods=["POST"])
def make_request():
    """Make an HTTP request through the browser.

    Request:
        {
            "url": "https://...",
            "method": "GET",
            "headers": {},
            "body": "...",
            "content_type": "application/json",
            "warmup_url": "https://...",
            "wait": 5,
            "wait_after": 0,
            "session_id": "...",
            "screenshot": false
        }
    """
    data = request.get_json() or {}
    url = data.get("url")
    if not url:
        return jsonify({"error": "url is required"}), 400

    def work():
        method = data.get("method", "GET").upper()
        headers = data.get("headers", {})
        body = data.get("body")
        content_type = data.get("content_type")
        warmup_url = data.get("warmup_url")
        wait = data.get("wait", 5)
        wait_after = data.get("wait_after", 0)
        session_id = data.get("session_id")
        take_screenshot = data.get("screenshot", False)

        session = _get_or_create_session(session_id)
        page = session.page

        if warmup_url and not session.challenge_solved:
            session.solve_challenge(warmup_url, wait)

        if headers:
            page.set_extra_http_headers(headers)

        if method == "GET":
            response = page.goto(url, timeout=30000)
            if response.status == 403 and not session.challenge_solved:
                time.sleep(wait)
                session.challenge_solved = True
                response = page.goto(url, timeout=30000)

            if wait_after:
                time.sleep(wait_after)

            result = {
                "status": response.status,
                "title": page.title(),
                "url": page.url,
                "body": page.content()[:50000],
                "cookies": session.get_cookies(),
                "session_id": session.id,
            }
        else:
            # Non-GET: use fetch() inside the browser
            fetch_headers = dict(headers)
            if content_type:
                fetch_headers["Content-Type"] = content_type

            # Make sure we're on the right domain
            current_domain = page.url.split("/")[2] if "://" in page.url else ""
            target_domain = url.split("/")[2] if "://" in url else ""
            if current_domain != target_domain:
                page.goto("/".join(url.split("/")[:3]), timeout=30000)
                time.sleep(2)

            body_arg = f"body: {json.dumps(body)}," if body else ""
            fetch_script = f"""
            async () => {{
                const resp = await fetch({json.dumps(url)}, {{
                    method: {json.dumps(method)},
                    headers: {json.dumps(fetch_headers)},
                    {body_arg}
                    credentials: 'include'
                }});
                const text = await resp.text();
                return {{
                    status: resp.status,
                    statusText: resp.statusText,
                    headers: Object.fromEntries(resp.headers.entries()),
                    body: text
                }};
            }}
            """
            fetch_result = page.evaluate(fetch_script)

            if wait_after:
                time.sleep(wait_after)

            result = {
                "status": fetch_result["status"],
                "title": page.title(),
                "url": url,
                "body": fetch_result["body"][:50000],
                "response_headers": fetch_result.get("headers", {}),
                "cookies": session.get_cookies(),
                "session_id": session.id,
            }

        if take_screenshot:
            result["screenshot"] = base64.b64encode(page.screenshot()).decode()

        session.touch()
        return result

    try:
        return jsonify(_run_on_browser_thread(work))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/execute", methods=["POST"])
def execute_js():
    """Execute JavaScript on the current page.

    Request: {"session_id": "...", "script": "document.title", "url": "..." (optional)}
    Returns: {"result": ..., "session_id": "..."}
    """
    data = request.get_json() or {}
    session_id = data.get("session_id")
    script = data.get("script")
    nav_url = data.get("url")

    if not script:
        return jsonify({"error": "script is required"}), 400
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    def work():
        session = _get_or_create_session(session_id)
        if nav_url:
            session.page.goto(nav_url, timeout=30000)
        result = session.page.evaluate(script)
        session.touch()
        return {"result": result, "session_id": session.id}

    try:
        return jsonify(_run_on_browser_thread(work))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/screenshot", methods=["POST"])
def screenshot():
    """Take a screenshot of the current page.

    Request: {"session_id": "...", "url": "..." (optional), "full_page": false}
    Returns: {"screenshot": "base64...", "title": "...", "url": "..."}
    """
    data = request.get_json() or {}
    session_id = data.get("session_id")
    nav_url = data.get("url")
    full_page = data.get("full_page", False)

    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    def work():
        session = _get_or_create_session(session_id)
        if nav_url:
            session.page.goto(nav_url, timeout=30000)
        img = session.page.screenshot(full_page=full_page)
        session.touch()
        return {
            "screenshot": base64.b64encode(img).decode(),
            "title": session.page.title(),
            "url": session.page.url,
            "session_id": session.id,
        }

    try:
        return jsonify(_run_on_browser_thread(work))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cookies", methods=["POST"])
def get_cookies():
    """Get cookies from a session.

    Request: {"session_id": "...", "url": "...", "warmup_url": "...", "wait": 5}
    """
    data = request.get_json() or {}
    session_id = data.get("session_id")
    nav_url = data.get("url")
    warmup_url = data.get("warmup_url")
    wait = data.get("wait", 5)

    def work():
        session = _get_or_create_session(session_id)
        if warmup_url and not session.challenge_solved:
            session.solve_challenge(warmup_url, wait)
        status = 200
        if nav_url:
            response = session.page.goto(nav_url, timeout=30000)
            status = response.status
        session.touch()
        return {
            "status": status,
            "title": session.page.title(),
            "cookies": session.get_cookies(),
            "session_id": session.id,
        }

    try:
        return jsonify(_run_on_browser_thread(work))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Browser proxy for security auditing")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--timeout", type=int, default=600, help="Session idle timeout in seconds")
    args = parser.parse_args()
    SESSION_TIMEOUT = args.timeout

    # Start the browser worker thread
    _browser_thread = threading.Thread(target=_browser_worker, daemon=True)
    _browser_thread.start()

    logger.info(f"Starting browser proxy on {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)
