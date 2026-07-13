"""Tier 3 — live smoke test (opt-in, calls real Claude + Codex, costs money).

Runs the REAL pipeline against the deliberately-vulnerable fixture app in
tests/fixtures/vuln_app and asserts the hunters find at least one of the two
planted bugs (SQL injection / IDOR). This is the only tier that proves the
agents actually find real bugs — and that the ported Codex SDK works end-to-end
inside the pipeline.

Enable with:
    RUN_LIVE_E2E=1 pytest -m live tests/live -s

Requirements: `claude` and `codex` CLIs installed and authenticated, plus the
security toolchain (setup/ubuntu-24-04-setup.sh). Assertions are tolerance-based
(bug classes/counts), never exact model wording.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from bug_hunter.core.config import AppConfig
from bug_hunter.core.database import create_engagement, get_run, init_db, list_bugs
from bug_hunter.pipeline.orchestrator import PipelineOrchestrator
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

# Cost/speed knobs (env-overridable). Defaults favor a cheap, fast smoke run.
LIVE_AGENTS = [a.strip() for a in os.environ.get("LIVE_AGENTS", "codex").split(",") if a.strip()]
LIVE_EFFORT = os.environ.get("LIVE_EFFORT", "low").strip()
# gpt-5.5 by default: smaller models (e.g. gpt-5.4-mini) exercise the full SDK
# path fine but tend to self-suppress obvious findings under the "only new bugs"
# prompt, so they fail the find-assertion. Override with LIVE_CODEX_MODEL.
LIVE_CODEX_MODEL = os.environ.get("LIVE_CODEX_MODEL", "gpt-5.5").strip()
LIVE_CLAUDE_MODEL = os.environ.get("LIVE_CLAUDE_MODEL", "claude-haiku-4-5-20251001").strip()


def _base_config(tmp_path) -> AppConfig:
    cfg = AppConfig()
    cfg.pipeline.output_dir = str(tmp_path / "audit_output")
    cfg.pipeline.auto_install_tools = False
    cfg.pipeline.subagent_timeout = 900
    cfg.bug_hunter.agents = list(LIVE_AGENTS)
    cfg.bug_hunter.mode = "parallel"
    cfg.bug_hunter.iterations = 1
    # Small, fast models everywhere.
    cfg.bug_hunter.codex_model = LIVE_CODEX_MODEL
    cfg.models.bug_hunter_subagent = LIVE_CLAUDE_MODEL
    for stage in ("scoper", "skills_hunter", "variant_hunter", "deduplicator",
                  "strict_validator", "perfectionist", "strict_triager", "bug_chainer"):
        setattr(cfg.models, stage, LIVE_CODEX_MODEL)
    if LIVE_EFFORT:
        cfg.pipeline.codex_reasoning_effort = LIVE_EFFORT
        cfg.pipeline.codex_reasoning_summary = "none"
    return cfg


def _vuln_engagement():
    return create_engagement("live-vuln-app", "source_code", {
        "engagement": {
            "type": "source_code",
            "source_path": str(VULN_APP),
            "scope_definition": "All code in this Flask app. Find security bugs.",
            "infra_config": "",
        },
    })


def _mentions_planted_vuln(bugs) -> tuple[bool, bool]:
    blob = json.dumps([b["bug_data"] for b in bugs]).lower()
    sqli = any(k in blob for k in ("sql injection", "sqli", "sql inject"))
    idor = any(k in blob for k in ("idor", "insecure direct object", "authorization", "access control"))
    return sqli, idor


async def test_live_bug_hunter_finds_planted_vuln(tmp_path):
    """Fast/minimal: runs ONLY the bug_hunter stage with real agents against the
    tiny fixture. One agentic session — the cheapest proof the SDK path works
    live end-to-end (schema-gated structured output, streaming, DB persistence)."""
    init_db(str(tmp_path / "db.sqlite"))
    cfg = _base_config(tmp_path)
    eng = _vuln_engagement()

    from bug_hunter.core.database import create_run
    run = create_run(eng["id"], run_type="initial")   # real run row so create_bug's FK holds
    run_id = run["id"]

    run_dir = os.path.join(cfg.pipeline.output_dir, "engagements", eng["id"], "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)
    ctx = StageContext(
        config=cfg, engagement_id=eng["id"], engagement=eng, run_id=run_id,
        run_dir=run_dir,
        cumulative_dir=os.path.join(cfg.pipeline.output_dir, "engagements", eng["id"], "cumulative"),
        run_type="initial",
    )

    result = await asyncio.wait_for(BugHunterStage().execute(ctx), timeout=1200)
    assert result.success, result.error

    bugs = list_bugs(eng["id"])
    assert bugs, "bug_hunter found nothing on an app with two obvious planted vulns"
    sqli, idor = _mentions_planted_vuln(bugs)
    assert sqli or idor, f"expected SQLi/IDOR; got: {json.dumps([b['bug_data'] for b in bugs])[:800]}"
    print(f"\nlive bug_hunter: {len(bugs)} finding(s); sqli={sqli} idor={idor}; "
          f"codex_model={cfg.bug_hunter.codex_model} effort={cfg.pipeline.codex_reasoning_effort}")


async def test_live_full_pipeline_finds_planted_bugs(tmp_path):
    """Thorough: the whole pipeline to completion. Slower/pricier — enable with
    RUN_LIVE_FULL=1 on top of RUN_LIVE_E2E=1."""
    if os.environ.get("RUN_LIVE_FULL") != "1":
        pytest.skip("full live pipeline disabled; set RUN_LIVE_FULL=1 (slow, pricier)")

    init_db(str(tmp_path / "db.sqlite"))
    cfg = _base_config(tmp_path)
    # Skip source-only expansion + optional escalation stages to keep it focused.
    cfg.skills_hunter.enabled = False
    cfg.variant_hunter.enabled = False
    cfg.perfectionist.enabled = False
    cfg.bug_chainer.enabled = False

    eng = _vuln_engagement()
    orch = PipelineOrchestrator(cfg, eng["id"])
    run_id = await orch.start_run(run_type="initial")
    assert orch._current_task is not None
    await asyncio.wait_for(orch._current_task, timeout=3600)

    run = get_run(run_id)
    assert run["status"] == "completed", f"pipeline did not complete: {run['status']}"
    bugs = list_bugs(eng["id"])
    assert bugs, "no bugs found at all"
    sqli, idor = _mentions_planted_vuln(bugs)
    assert sqli or idor, f"expected SQLi or IDOR; got: {json.dumps([b['bug_data'] for b in bugs])[:800]}"
    assert all(b["bug_data"].get("description") for b in bugs)
    print(f"\nlive full pipeline: {len(bugs)} finding(s); sqli={sqli} idor={idor}; "
          f"agents={cfg.bug_hunter.agents} run_cost_usd={run.get('cost_usd')}")
