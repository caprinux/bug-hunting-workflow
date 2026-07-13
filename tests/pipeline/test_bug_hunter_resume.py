"""Tier 1 — the Codex thread / Claude session resume behavior we added.

Drives BugHunterStage.execute() directly (twice) with the replay backend so we
can assert exactly what session/thread id and is_resume/thread_id are threaded
into the agent calls across runs.
"""

from __future__ import annotations

import json
import os

import pytest

from bug_hunter.core.database import create_run
from bug_hunter.pipeline.stages.base import StageContext
from bug_hunter.pipeline.stages.bug_hunter import BugHunterStage

from tests.pipeline.replay import ReplayBackend, canned_bug


def _context(cfg, eng, run_id) -> StageContext:
    run_dir = os.path.join(cfg.pipeline.output_dir, "engagements", eng["id"], "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)
    return StageContext(
        config=cfg,
        engagement_id=eng["id"],
        engagement=eng,
        run_id=run_id,
        run_dir=run_dir,
        cumulative_dir=os.path.join(cfg.pipeline.output_dir, "engagements", eng["id"], "cumulative"),
        run_type="initial",
    )


async def _run_once(cfg, eng, run_type="initial", rehunt_target=None):
    run = create_run(eng["id"], run_type=run_type)
    ctx = _context(cfg, eng, run["id"])
    ctx.run_type = run_type
    ctx.rehunt_target = rehunt_target
    result = await BugHunterStage().execute(ctx)
    return ctx, result


@pytest.fixture
def backend(monkeypatch) -> ReplayBackend:
    b = ReplayBackend()
    b.set("bug_hunter:claude", {"bugs": [canned_bug("SQL Injection")]})
    b.set("bug_hunter:codex", {"bugs": [canned_bug("IDOR")]})
    b.install(monkeypatch)
    return b


def _sessions(cfg, eng) -> dict:
    path = os.path.join(cfg.pipeline.output_dir, "engagements", eng["id"], "agent_sessions.json")
    return json.load(open(path)) if os.path.exists(path) else {}


async def test_first_run_starts_fresh_then_persists_ids(make_engagement, fixture_repo, app_config, backend):
    cfg = app_config
    cfg.bug_hunter.agents = ["claude", "codex"]
    cfg.bug_hunter.mode = "parallel"
    eng = make_engagement(str(fixture_repo))

    await _run_once(cfg, eng)

    # First run: neither agent resumes.
    claude_call = backend.calls_for("bug_hunter:claude")[0]
    codex_call = backend.calls_for("bug_hunter:codex")[0]
    assert claude_call["is_resume"] is False
    assert codex_call["thread_id"] is None

    # Both ids are persisted at engagement level and marked used.
    sessions = _sessions(cfg, eng)
    assert sessions.get("bug_hunter_claude") and sessions.get("bug_hunter_claude_used") is True
    assert sessions.get("bug_hunter_codex") and sessions.get("bug_hunter_codex_used") is True


async def test_second_run_resumes_codex_thread_and_claude_session(make_engagement, fixture_repo, app_config, backend):
    cfg = app_config
    cfg.bug_hunter.agents = ["claude", "codex"]
    cfg.bug_hunter.mode = "parallel"
    eng = make_engagement(str(fixture_repo))

    await _run_once(cfg, eng)                       # run 1 establishes the sessions
    saved = _sessions(cfg, eng)
    codex_thread = saved["bug_hunter_codex"]
    claude_session = saved["bug_hunter_claude"]

    backend.calls.clear()
    await _run_once(cfg, eng, run_type="rehunt", rehunt_target="focus on auth")  # run 2 resumes

    codex_call = backend.calls_for("bug_hunter:codex")[0]
    claude_call = backend.calls_for("bug_hunter:claude")[0]
    # Codex is resumed via its persisted thread id.
    assert codex_call["thread_id"] == codex_thread
    # Claude resumes its session (and omits the system-prompt agent_file on resume).
    assert claude_call["is_resume"] is True
    assert claude_call["session_id"] == claude_session
    assert claude_call.get("agent_file") is None


async def test_codex_resume_failure_retries_fresh(make_engagement, fixture_repo, app_config, backend):
    cfg = app_config
    cfg.bug_hunter.agents = ["codex"]              # isolate codex
    cfg.bug_hunter.mode = "parallel"
    eng = make_engagement(str(fixture_repo))

    await _run_once(cfg, eng)                       # establish a codex thread
    codex_thread = _sessions(cfg, eng)["bug_hunter_codex"]

    # On the next run, the first (resume) call fails with a rollout-missing error;
    # the stage must drop the stale id and retry with a fresh thread.
    backend.calls.clear()
    backend.fail_once["bug_hunter:codex"] = (
        f"JSON-RPC error -32600: no rollout found for thread id {codex_thread}"
    )
    ctx, result = await _run_once(cfg, eng, run_type="rehunt", rehunt_target="again")

    codex_calls = backend.calls_for("bug_hunter:codex")
    assert len(codex_calls) == 2                    # failed resume + fresh retry
    assert codex_calls[0]["thread_id"] == codex_thread   # attempted resume
    assert codex_calls[1]["thread_id"] is None           # retried fresh
    assert result.success
