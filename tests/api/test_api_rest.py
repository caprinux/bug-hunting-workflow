"""Tier 2 — REST API integration against the real FastAPI app (offline).

Drives the app in-process with httpx.AsyncClient (so the orchestrator's
background pipeline task runs on the test's event loop and can be awaited).
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest

import bug_hunter.api.routes as routes


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_health_open_without_auth(api):
    async with _client(api) as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_create_engagement_via_api(api):
    async with _client(api) as c:
        r = await c.post("/api/engagements", json={
            "name": "api-eng", "type": "source_code",
            "source_path": "/tmp/whatever", "scope_definition": "all",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "api-eng"
    assert body["type"] == "source_code"
    assert body["id"]


async def test_create_engagement_rejects_bad_type(api):
    async with _client(api) as c:
        r = await c.post("/api/engagements", json={"name": "x", "type": "nonsense"})
    assert r.status_code == 422  # regex-validated enum


async def test_start_run_completes_and_exposes_bugs(api, fixture_repo):
    async with _client(api) as c:
        # single agent keeps the run small; disable source-only extra hunters
        r = await c.post("/api/engagements", json={
            "name": "run-eng", "type": "source_code",
            "source_path": str(fixture_repo), "scope_definition": "all",
            "config_overrides": {
                "bug_hunter": {"agents": ["claude"]},
                "skills_hunter": {"enabled": False},
                "variant_hunter": {"enabled": False},
            },
        })
        eng_id = r.json()["id"]

        r = await c.post(f"/api/engagements/{eng_id}/runs", json={"run_type": "initial"})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]

        # Await the background pipeline task the route spawned.
        orch = routes._orchestrators[eng_id]
        await asyncio.wait_for(orch._current_task, timeout=30)

        r = await c.get(f"/api/engagements/{eng_id}/runs/{run_id}")
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

        r = await c.get(f"/api/engagements/{eng_id}/bugs")
        bugs = r.json()
    assert len(bugs) == 1
    assert bugs[0]["bug_data"]["vuln_type"] == "SQL Injection"


async def test_stage_stream_endpoint_parses_codex_events(api, fixture_repo, temp_config):
    """Write a codex-format stream.jsonl and confirm the stream endpoint's
    parser (routes.py) reconstructs text/tool_use/thinking events — this guards
    the recorded schema the SDK port preserves."""
    async with _client(api) as c:
        r = await c.post("/api/engagements", json={
            "name": "stream-eng", "type": "source_code",
            "source_path": str(fixture_repo), "scope_definition": "all",
        })
        eng_id = r.json()["id"]
        # create a run row so list_stage_results resolves the stage_order
        from bug_hunter.core.database import create_run, create_stage_result
        run = create_run(eng_id, run_type="initial")
        run_id = run["id"]
        create_stage_result(run_id, "bug_hunter", 3)

        # Lay down a stream.jsonl in the exact location the endpoint globs.
        out = temp_config.pipeline.output_dir
        agent_dir = os.path.join(
            out, "engagements", eng_id, "runs", run_id,
            "03_bug_hunter", "agent_runs", "20260101T000000000000Z_codex_bug_hunt_abcd1234",
        )
        os.makedirs(agent_dir, exist_ok=True)
        lines = [
            {"type": "codex_event", "event_type": "item_completed",
             "item_type": "agent_message", "text": '{"narrative":"probing login","bugs":[]}'},
            {"type": "codex_event", "event_type": "item_completed",
             "item_type": "command_execution", "command": "curl http://x/login"},
            {"type": "codex_event", "event_type": "item_completed",
             "item_type": "reasoning", "text": "considering an auth bypass"},
        ]
        with open(os.path.join(agent_dir, "stream.jsonl"), "w") as f:
            for ln in lines:
                f.write(json.dumps({"timestamp": "t", "raw": json.dumps(ln)}) + "\n")

        r = await c.get(f"/api/engagements/{eng_id}/runs/{run_id}/stages/bug_hunter/stream")
    assert r.status_code == 200, r.text
    events = r.json()["events"]
    kinds = {(e["event_type"], e.get("text") or e.get("tool_name") or e.get("thinking")) for e in events}
    assert ("text", "probing login") in kinds
    assert any(e["event_type"] == "tool_use" and e["tool_name"] == "Bash" for e in events)
    assert any(e["event_type"] == "thinking" and "auth bypass" in e["thinking"] for e in events)
    # agent id parsed from the dir name (parts[1])
    assert all(e["agent_id"] == "codex" for e in events)
