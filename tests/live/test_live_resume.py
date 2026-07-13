"""Tier 3 (live) — parallel-mode session resume, proven through the real SDKs.

Runs the bug_hunter stage twice in parallel mode and asserts the second run
RESUMES the exact conversation the first run created, for BOTH agents:
  - Codex: run 2 is invoked with run 1's thread_id  (recorded in request.json)
  - Claude: run 2 is invoked with is_resume=True + run 1's session_id, and the
            system-prompt agent_file is omitted on resume.

This validates resume end-to-end through the actual code path — not the replay
stubs. It doesn't rely on finding bugs, so cheap models are fine:

    RUN_LIVE_E2E=1 pytest -m live tests/live/test_live_resume.py -s
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
from pathlib import Path

import pytest

from bug_hunter.core.config import AppConfig
from bug_hunter.core.database import create_engagement, create_run, init_db
from bug_hunter.pipeline.stages.base import StageContext
from bug_hunter.pipeline.stages.bug_hunter import BugHunterStage

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_E2E") != "1",
        reason="live e2e disabled; set RUN_LIVE_E2E=1 (needs authenticated claude+codex)",
    ),
]

VULN_APP = Path(__file__).resolve().parents[1] / "fixtures" / "vuln_app"
LIVE_CODEX_MODEL = os.environ.get("LIVE_CODEX_MODEL", "gpt-5.4-mini").strip()
LIVE_CLAUDE_MODEL = os.environ.get("LIVE_CLAUDE_MODEL", "claude-haiku-4-5-20251001").strip()
LIVE_EFFORT = os.environ.get("LIVE_EFFORT", "low").strip()


def _config(tmp_path, agent: str) -> AppConfig:
    cfg = AppConfig()
    cfg.pipeline.output_dir = str(tmp_path / "audit_output")
    cfg.pipeline.auto_install_tools = False
    cfg.pipeline.subagent_timeout = 900
    cfg.pipeline.codex_reasoning_effort = LIVE_EFFORT
    cfg.pipeline.codex_reasoning_summary = "none"
    cfg.bug_hunter.agents = [agent]        # one real agent
    cfg.bug_hunter.mode = "parallel"       # <-- resume is parallel-only
    cfg.bug_hunter.iterations = 1
    cfg.bug_hunter.codex_model = LIVE_CODEX_MODEL
    cfg.models.bug_hunter_subagent = LIVE_CLAUDE_MODEL
    return cfg


def _sessions(cfg, eng) -> dict:
    p = os.path.join(cfg.pipeline.output_dir, "engagements", eng["id"], "agent_sessions.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def _request(run_dir: str) -> dict:
    """The recorded request.json run_claude/run_codex was invoked with."""
    reqs = glob.glob(os.path.join(run_dir, "03_bug_hunter", "agent_runs", "*", "request.json"))
    assert reqs, f"no recorded agent request under {run_dir}"
    return json.load(open(reqs[0]))


async def _run(cfg, eng, run_type):
    run = create_run(eng["id"], run_type=run_type)
    run_dir = os.path.join(cfg.pipeline.output_dir, "engagements", eng["id"], "runs", run["id"])
    os.makedirs(run_dir, exist_ok=True)
    ctx = StageContext(
        config=cfg, engagement_id=eng["id"], engagement=eng, run_id=run["id"],
        run_dir=run_dir,
        cumulative_dir=os.path.join(cfg.pipeline.output_dir, "engagements", eng["id"], "cumulative"),
        run_type=run_type,
        rehunt_target="Look again for anything missed." if run_type == "rehunt" else None,
    )
    result = await asyncio.wait_for(BugHunterStage().execute(ctx), timeout=1200)
    assert result.success, result.error
    return run_dir


@pytest.mark.parametrize("agent", ["codex", "claude"])
async def test_live_parallel_mode_resumes_session(tmp_path, agent):
    init_db(str(tmp_path / "db.sqlite"))
    cfg = _config(tmp_path, agent)
    eng = create_engagement("live-resume", "source_code", {
        "engagement": {"type": "source_code", "source_path": str(VULN_APP),
                       "scope_definition": "Find security bugs.", "infra_config": ""},
    })

    # Run 1 (initial): fresh conversation, persisted + marked used.
    run1_dir = await _run(cfg, eng, "initial")
    req1 = _request(run1_dir)
    sessions = _sessions(cfg, eng)
    saved = sessions.get(f"bug_hunter_{agent}")
    assert saved and sessions.get(f"bug_hunter_{agent}_used") is True

    if agent == "codex":
        assert req1.get("thread_id") in (None, ""), "codex run 1 should start a fresh thread"
    else:
        assert req1.get("is_resume") is False, "claude run 1 should not be a resume"

    # Run 2 (rehunt): must resume run 1's real conversation.
    run2_dir = await _run(cfg, eng, "rehunt")
    req2 = _request(run2_dir)

    if agent == "codex":
        assert req2.get("thread_id") == saved, \
            f"codex run 2 did not resume thread: {req2.get('thread_id')!r} != {saved!r}"
    else:
        assert req2.get("is_resume") is True, "claude run 2 should be a resume"
        assert req2.get("session_id") == saved, \
            f"claude run 2 did not resume session: {req2.get('session_id')!r} != {saved!r}"
        # system prompt (agent_file) is omitted on resume
        assert not req2.get("agent_file"), "claude should omit agent_file on resume"

    assert _sessions(cfg, eng)[f"bug_hunter_{agent}"] == saved  # stable across runs
    print(f"\nlive parallel resume OK [{agent}]: run1 conversation {saved} reused in run2")
