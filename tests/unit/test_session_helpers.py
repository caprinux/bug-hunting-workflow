"""Tier 0 — unit tests for the bug_hunter persistent-session helpers.

These drive the exact logic that makes Claude sessions and Codex threads
resume across re-hunts (parallel mode).
"""

from __future__ import annotations

import json
import os

import pytest

from bug_hunter.pipeline.stages.bug_hunter import BugHunterStage


class _Ctx:
    """Minimal stand-in for StageContext for the session helpers.

    _get_sessions_file walks two dirs up from run_dir to the engagement dir,
    so run_dir just needs two parent levels.
    """
    def __init__(self, eng_dir: str):
        self.run_dir = os.path.join(eng_dir, "runs", "run1")
        os.makedirs(self.run_dir, exist_ok=True)


@pytest.fixture
def stage():
    return BugHunterStage()


def test_first_call_is_fresh_not_resume(tmp_path, stage):
    ctx = _Ctx(str(tmp_path))
    sid, is_resume = stage._get_agent_session(ctx, "codex")
    assert is_resume is False
    assert sid  # a placeholder id is generated


def test_unused_saved_id_is_replaced_not_resumed(tmp_path, stage):
    # A saved id that was never marked used (e.g. a failed attempt) must not
    # resume — it is discarded and a fresh id generated.
    ctx = _Ctx(str(tmp_path))
    sessions = stage._load_sessions(ctx)
    sessions["bug_hunter_codex"] = "stale-thread"
    stage._save_sessions(ctx, sessions)

    sid, is_resume = stage._get_agent_session(ctx, "codex")
    assert is_resume is False
    assert sid != "stale-thread"


def test_resumes_when_id_and_used_set(tmp_path, stage):
    # Mirrors the post-success end-of-run state: the real thread id is saved and
    # marked used together. The next run must resume it.
    ctx = _Ctx(str(tmp_path))
    sessions = stage._load_sessions(ctx)
    sessions["bug_hunter_codex"] = "thread-abc"
    stage._save_sessions(ctx, sessions)
    stage._mark_session_used(ctx, "codex")

    sid, is_resume = stage._get_agent_session(ctx, "codex")
    assert is_resume is True
    assert sid == "thread-abc"


def test_sessions_persist_at_engagement_level(tmp_path, stage):
    ctx = _Ctx(str(tmp_path))
    stage._get_agent_session(ctx, "codex")
    # File lives at the engagement dir (two levels up from run_dir), so it
    # survives across runs.
    assert (tmp_path / "agent_sessions.json").exists()


def test_claude_and_codex_sessions_are_independent(tmp_path, stage):
    ctx = _Ctx(str(tmp_path))
    for agent, tid in (("claude", "sess-claude"), ("codex", "thread-codex")):
        stage._get_agent_session(ctx, agent)
        s = stage._load_sessions(ctx)
        s[f"bug_hunter_{agent}"] = tid
        stage._save_sessions(ctx, s)
        stage._mark_session_used(ctx, agent)

    sid_c, r_c = stage._get_agent_session(ctx, "claude")
    sid_x, r_x = stage._get_agent_session(ctx, "codex")
    assert (sid_c, r_c) == ("sess-claude", True)
    assert (sid_x, r_x) == ("thread-codex", True)
