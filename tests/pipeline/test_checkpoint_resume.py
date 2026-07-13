"""Tier 1 — pipeline checkpoint / resume.

Cancels a run partway through, then resumes it, and asserts the pipeline picks
up where it left off (already-completed stages are not re-run) and reaches a
completed terminal state.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from bug_hunter.core.database import get_run, list_bugs, list_stage_results
from bug_hunter.pipeline.orchestrator import PipelineOrchestrator

from tests.pipeline.replay import ReplayBackend, canned_bug, validated_poc


@pytest.fixture
def backend(monkeypatch) -> ReplayBackend:
    b = ReplayBackend()
    b.set("bug_hunter:claude", {"bugs": [canned_bug("SQL Injection")]})
    b.set("skills_hunter", {"narrative": "", "bugs": []})
    b.set("scope_validator", {"narrative": "", "in_scope": [], "out_of_scope": []})
    b.set("strict_validator", {
        "narrative": "", "validated": True, "verdict": "confirmed",
        "poc": validated_poc(), "reason": "",
    })
    b.set("strict_triager", {"narrative": "", "tagged": []})
    b.install(monkeypatch)
    return b


async def test_cancel_then_resume_completes_without_rerunning_bug_hunter(
    make_engagement, fixture_repo, app_config, backend, monkeypatch
):
    cfg = app_config
    cfg.bug_hunter.agents = ["claude"]        # single agent, no dedup
    cfg.skills_hunter.enabled = False
    cfg.variant_hunter.enabled = False

    eng = make_engagement(str(fixture_repo))
    orch = PipelineOrchestrator(cfg, eng["id"])

    # Make the scope_validator stage cancel the run the first time it runs, so
    # the pipeline stops with setup + bug_hunter already completed.
    original = backend._make_fake("scope_validator")

    calls = {"n": 0}

    async def cancel_on_first_scope(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            await orch.cancel_run()
            raise asyncio.CancelledError()
        return await original(*args, **kwargs)

    monkeypatch.setattr(
        "bug_hunter.pipeline.stages.scope_validator.run_agent", cancel_on_first_scope
    )

    run_id = await orch.start_run(run_type="initial")
    if orch._current_task is not None:
        with pytest.raises((asyncio.CancelledError, Exception)):
            await orch._current_task

    run = get_run(run_id)
    assert run["status"] == "cancelled", run

    # bug_hunter completed before the cancel and already persisted its finding.
    stages = {s["stage_name"]: s["status"] for s in list_stage_results(run_id)}
    assert stages.get("bug_hunter") == "completed"
    assert len(list_bugs(eng["id"])) == 1

    # Resume: bug_hunter must NOT run again (it's in completed_stages).
    backend.calls.clear()
    ok = await orch.resume_run(run_id)
    assert ok is True
    if orch._current_task is not None:
        await asyncio.wait_for(orch._current_task, timeout=30)

    run = get_run(run_id)
    assert run["status"] == "completed", run
    assert backend.calls_for("bug_hunter:claude") == []   # not re-invoked on resume

    # Still exactly one bug (no duplicate from a re-run), and it's confirmed.
    bugs = list_bugs(eng["id"])
    assert len(bugs) == 1
    assert bugs[0]["status"] in ("confirmed", "informational")
