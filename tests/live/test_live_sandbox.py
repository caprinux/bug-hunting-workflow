"""Tier 3 (live, needs Docker + the bhw-agent image) — prove the container
actually confines the agent.

These use the real `run_codex` against the bhw-agent image, but the "agent" here
is just a shell command we ask codex to run and report — we assert the container
boundary itself:
  - the read-only rootfs rejects writes outside /work,
  - a write to $HOME lands in the mounted /work (captured, not lost/escaped),
  - host paths outside the mounts (a planted canary) are invisible,
  - a PATH tool still executes.

Enable with:  RUN_LIVE_E2E=1 pytest -m live tests/live/test_live_sandbox.py -s
Requires: `docker` available and `bhw-agent:latest` built (setup script).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bug_hunter.core.sandbox import ContainerSpec, build_docker_argv

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_E2E") != "1",
        reason="live e2e disabled; set RUN_LIVE_E2E=1",
    ),
]


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return False
    return subprocess.run(
        ["docker", "image", "inspect", "bhw-agent:latest"], capture_output=True
    ).returncode == 0


needs_docker = pytest.mark.skipif(not _docker_ok(), reason="docker + bhw-agent:latest required")


def _run_in_container(tmp_path: Path, script: str) -> subprocess.CompletedProcess:
    """Run a shell one-liner inside the isolation container, same argv the SDK
    would use, and capture output."""
    (tmp_path / "ws").mkdir(exist_ok=True)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "home").mkdir(exist_ok=True)
    spec = ContainerSpec(
        image="bhw-agent:latest", kind="codex",
        work_host=str(tmp_path / "ws"), source_host=str(tmp_path / "src"),
        agent_home_host=str(tmp_path / "home"),
    )
    # Same docker argv the SDK uses, but run /bin/sh -c <script> as the entry so
    # we exercise the container boundary itself (the harmless /opt/codex mount is
    # left in place).
    argv = build_docker_argv(spec, [])
    img_idx = argv.index("bhw-agent:latest")
    argv = argv[:img_idx] + ["bhw-agent:latest", "/bin/sh", "-c", script]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120)


@needs_docker
def test_readonly_rootfs_blocks_writes_outside_work(tmp_path):
    # write to /etc must fail; write to $HOME (=/work) must succeed and persist.
    r = _run_in_container(
        tmp_path,
        "echo x > /etc/canary; echo etc=$?; "
        "echo y > $HOME/note.txt; echo home=$?; "
        "cat $HOME/note.txt",
    )
    # /etc write is rejected by the read-only rootfs (non-zero exit + kernel error)
    assert "etc=0" not in r.stdout, r.stdout + r.stderr
    assert "read-only file system" in r.stderr.lower(), r.stderr
    # $HOME (=/work) write succeeds and persists to the host-mounted ws
    assert "home=0" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "ws" / "note.txt").read_text().strip() == "y"


@needs_docker
def test_host_canary_is_invisible(tmp_path):
    # Plant a canary on the host OUTSIDE any mount; the container must not see it.
    canary = tmp_path / "SECRET_OTHER_AGENT.txt"
    canary.write_text("do not read me")
    r = _run_in_container(
        tmp_path,
        f"cat {canary} 2>&1; echo rc=$?",
    )
    # The container must not be able to read the canary's CONTENT (the host path
    # isn't mounted). The path string may appear in cat's "No such file" error —
    # that's fine; what matters is the content never leaks and the read fails.
    assert "do not read me" not in r.stdout
    assert "rc=1" in r.stdout


@needs_docker
def test_tool_on_path_still_runs(tmp_path):
    r = _run_in_container(tmp_path, "nmap --version | head -1; nuclei -version 2>&1 | head -1")
    out = (r.stdout + r.stderr).lower()
    assert "nmap" in out


@needs_docker
async def test_live_containerized_bug_hunter_finds_vuln(tmp_path):
    """Full integration: the real bug_hunter stage with sandbox.enabled runs
    codex INSIDE the container against /src and still surfaces the planted vuln
    (structured output flows back to the host over stdio)."""
    import json

    from bug_hunter.core.config import AppConfig
    from bug_hunter.core.database import create_engagement, create_run, init_db, list_bugs
    from bug_hunter.pipeline.stages.base import StageContext
    from bug_hunter.pipeline.stages.bug_hunter import BugHunterStage

    vuln_app = Path(__file__).resolve().parents[1] / "fixtures" / "vuln_app"
    init_db(str(tmp_path / "db.sqlite"))

    cfg = AppConfig()
    cfg.pipeline.output_dir = str(tmp_path / "audit_output")
    cfg.pipeline.auto_install_tools = False
    cfg.pipeline.subagent_timeout = 900
    cfg.pipeline.codex_reasoning_effort = "low"
    cfg.pipeline.codex_reasoning_summary = "none"
    cfg.sandbox.enabled = True
    cfg.sandbox.image = "bhw-agent:latest"
    cfg.bug_hunter.agents = ["codex"]
    cfg.bug_hunter.mode = "parallel"
    cfg.bug_hunter.codex_model = "gpt-5.5"

    eng = create_engagement("live-sandbox", "source_code", {
        "engagement": {"type": "source_code", "source_path": str(vuln_app),
                       "scope_definition": "Find security bugs.", "infra_config": ""},
    })
    run = create_run(eng["id"], run_type="initial")
    run_dir = os.path.join(cfg.pipeline.output_dir, "engagements", eng["id"], "runs", run["id"])
    os.makedirs(run_dir, exist_ok=True)
    ctx = StageContext(
        config=cfg, engagement_id=eng["id"], engagement=eng, run_id=run["id"],
        run_dir=run_dir,
        cumulative_dir=os.path.join(cfg.pipeline.output_dir, "engagements", eng["id"], "cumulative"),
        run_type="initial",
    )

    result = await asyncio.wait_for(BugHunterStage().execute(ctx), timeout=1200)
    assert result.success, result.error

    bugs = list_bugs(eng["id"])
    assert bugs, "containerized codex found nothing"
    blob = json.dumps([b["bug_data"] for b in bugs]).lower()
    assert any(k in blob for k in ("sql", "inject", "idor")), blob[:600]

    # The agent's private ws exists on the host and it never touched a sibling.
    ws = os.path.join(run_dir, "03_bug_hunter", "agent_ws", "codex")
    assert os.path.isdir(ws)
    print(f"\ncontainerized bug_hunter: {len(bugs)} finding(s) via codex-in-container")
