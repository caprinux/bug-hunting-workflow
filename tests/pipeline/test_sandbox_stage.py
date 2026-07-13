"""Tier 1 — bug_hunter stage wiring for container isolation (offline).

Runs the real bug_hunter stage with sandbox.enabled=True but the agent runner
replaced by the replay backend, so we can assert the stage builds a private
per-agent workspace and hands the runner a correct ContainerSpec + a prompt that
references only the in-container paths (no shared-file leakage).
"""

from __future__ import annotations

import os

import pytest

from bug_hunter.core.database import create_run
from bug_hunter.core.sandbox import ContainerSpec
from bug_hunter.pipeline.stages.base import StageContext
from bug_hunter.pipeline.stages.bug_hunter import BugHunterStage

from tests.pipeline.replay import ReplayBackend, canned_bug


@pytest.fixture
def backend(monkeypatch) -> ReplayBackend:
    b = ReplayBackend()
    b.set("bug_hunter:claude", {"bugs": [canned_bug("SQL Injection")]})
    b.set("bug_hunter:codex", {"bugs": [canned_bug("IDOR")]})
    b.install(monkeypatch)
    return b


def _context(cfg, eng):
    run = create_run(eng["id"], run_type="initial")
    run_dir = os.path.join(cfg.pipeline.output_dir, "engagements", eng["id"], "runs", run["id"])
    os.makedirs(run_dir, exist_ok=True)
    return StageContext(
        config=cfg, engagement_id=eng["id"], engagement=eng, run_id=run["id"],
        run_dir=run_dir,
        cumulative_dir=os.path.join(cfg.pipeline.output_dir, "engagements", eng["id"], "cumulative"),
        run_type="initial",
    )


async def test_stage_builds_container_and_private_ws(make_engagement, fixture_repo, app_config, backend):
    cfg = app_config
    cfg.sandbox.enabled = True
    cfg.bug_hunter.agents = ["claude", "codex"]
    cfg.bug_hunter.mode = "parallel"
    eng = make_engagement(str(fixture_repo))
    ctx = _context(cfg, eng)

    await BugHunterStage().execute(ctx)

    stage_dir = os.path.join(ctx.run_dir, "03_bug_hunter")
    for agent in ("claude", "codex"):
        call = backend.calls_for(f"bug_hunter:{agent}")[0]
        spec = call["container"]
        assert isinstance(spec, ContainerSpec)
        assert spec.kind == agent

        # Private per-agent workspace, seeded with its OWN notes/surfaces.
        ws = os.path.join(stage_dir, "agent_ws", agent)
        assert spec.work_host == ws
        assert os.path.exists(os.path.join(ws, "NOTES.md"))
        assert os.path.exists(os.path.join(ws, "ATTACK_SURFACES.md"))

        # Source mounted read-only; persistent per-agent home for resume.
        assert spec.source_host == str(fixture_repo)
        assert spec.agent_home_host.endswith(os.path.join("agent_homes", agent))

        # Prompt uses in-container paths only — no shared engagement paths.
        prompt = call["prompt"]
        assert "/src" in prompt
        assert "/work/NOTES.md" in prompt
        assert "BUGS.json" not in prompt          # shared file not referenced
        assert str(fixture_repo) not in prompt     # no host source path leaked
        assert eng["id"] not in prompt             # no engagement-dir path leaked

    # Each agent's ws is distinct (they can't be pointed at each other).
    assert (backend.calls_for("bug_hunter:claude")[0]["container"].work_host
            != backend.calls_for("bug_hunter:codex")[0]["container"].work_host)


async def test_sandbox_disabled_passes_no_container(make_engagement, fixture_repo, app_config, backend):
    cfg = app_config
    cfg.sandbox.enabled = False
    cfg.bug_hunter.agents = ["codex"]
    eng = make_engagement(str(fixture_repo))
    ctx = _context(cfg, eng)

    await BugHunterStage().execute(ctx)

    call = backend.calls_for("bug_hunter:codex")[0]
    assert call.get("container") is None
