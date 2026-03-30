"""FastAPI REST API routes."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from bug_hunter.core.auth import issue_session_token
from bug_hunter.core.config import AppConfig, DEFAULT_CONFIG_PATH, config_to_dict, load_config
from bug_hunter.core.database import (
    create_engagement, get_engagement, list_engagements, update_engagement,
    delete_engagement,
    create_run, get_run, list_runs, list_stage_results,
    list_bugs, get_bug, list_chains,
)
from bug_hunter.core.models import (
    CreateEngagementRequest, StartRunRequest,
    EngagementResponse, RunResponse,
)
from bug_hunter.pipeline.orchestrator import PipelineOrchestrator

router = APIRouter(prefix="/api")

_orchestrators: dict[str, PipelineOrchestrator] = {}


def _engagement_output_dir(engagement: dict) -> str:
    """Resolve the output directory for an engagement from its stored config."""
    return (
        engagement.get("config", {})
        .get("pipeline", {})
        .get("output_dir")
        or load_config().pipeline.output_dir
    )


@router.get("/engagements")
async def api_list_engagements():
    engagements = list_engagements()
    for eng in engagements:
        eng["runs"] = list_runs(eng["id"])
        # Include bug counts for dashboard display
        all_bugs = list_bugs(eng["id"])
        active = [b for b in all_bugs if b["status"] in ("found", "confirmed", "validated")]
        confirmed_only = [b for b in all_bugs if b["status"] == "confirmed"]
        eng["bug_counts"] = {
            "total": len(all_bugs),
            "active": len(active),
            "confirmed": len(confirmed_only),
            "cannot_validate": sum(1 for b in all_bugs if b["status"] == "cannot_validate"),
        }
        # Severity breakdown of active bugs (found + confirmed + validated)
        sev = {}
        for b in active:
            s = b["bug_data"].get("severity", "unknown")
            sev[s] = sev.get(s, 0) + 1
        eng["bug_counts"]["by_severity"] = sev
    return engagements


@router.post("/auth/session")
async def api_create_session():
    """Mint a signed session token for API and WebSocket auth."""
    return {"token": issue_session_token()}


@router.post("/engagements")
async def api_create_engagement(req: CreateEngagementRequest):
    config = load_config()
    eng_config = config_to_dict(config)

    eng_config["engagement"] = {
        "type": req.type,
        "source_path": req.source_path,
        "source_repo": req.source_repo,
        "target_domains": req.target_domains,
        "scope_definition": req.scope_definition,
        "infra_config": req.infra_config,
    }

    # Apply engagement-type-specific defaults
    if req.type == "black_box":
        eng_config.setdefault("bug_hunter", {})
        eng_config["bug_hunter"].setdefault("agents", ["claude"])
        eng_config["bug_hunter"].setdefault("mode", "parallel")
    else:  # source_code
        eng_config.setdefault("bug_hunter", {})
        eng_config["bug_hunter"].setdefault("agents", ["claude", "codex"])
        eng_config["bug_hunter"].setdefault("mode", "parallel")

    if req.config_overrides and isinstance(req.config_overrides, dict):
        # Strip dangerous overrides that could allow path injection
        overrides = dict(req.config_overrides)
        if isinstance(overrides.get("pipeline"), dict):
            overrides["pipeline"].pop("output_dir", None)
        else:
            overrides.pop("pipeline", None)
        overrides.pop("auth", None)
        _deep_merge(eng_config, overrides)

    engagement = create_engagement(req.name, req.type, eng_config)

    output_dir = config.pipeline.output_dir
    eng_dir = os.path.join(output_dir, "engagements", engagement["id"])
    os.makedirs(os.path.join(eng_dir, "cumulative"), exist_ok=True)

    return engagement


@router.delete("/engagements/{engagement_id}")
async def api_delete_engagement(engagement_id: str):
    eng = get_engagement(engagement_id)
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")
    # Don't delete if a run is active
    active = [r for r in list_runs(engagement_id) if r["status"] == "running"]
    if active:
        raise HTTPException(status_code=409, detail="Cannot delete engagement with active runs")
    # Delete output files
    output_dir = _engagement_output_dir(eng)
    eng_dir = os.path.join(output_dir, "engagements", engagement_id)
    if os.path.isdir(eng_dir):
        import shutil
        shutil.rmtree(eng_dir, ignore_errors=True)
    delete_engagement(engagement_id)
    return {"status": "deleted"}


@router.get("/engagements/{engagement_id}")
async def api_get_engagement(engagement_id: str):
    eng = get_engagement(engagement_id)
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")
    eng["runs"] = list_runs(engagement_id)
    return eng


@router.patch("/engagements/{engagement_id}/notes")
async def api_update_engagement_notes(engagement_id: str, body: dict):
    """Update engagement notes."""
    eng = get_engagement(engagement_id)
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")
    return update_engagement(engagement_id, notes=body.get("notes", ""))


@router.patch("/engagements/{engagement_id}/config")
async def api_update_engagement_config(engagement_id: str, config_updates: dict):
    """Update engagement config between runs. Blocked while a run is active."""
    eng = get_engagement(engagement_id)
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")
    active = [r for r in list_runs(engagement_id) if r["status"] == "running"]
    if active:
        raise HTTPException(status_code=409, detail="Cannot modify config while a run is active")

    current_config = eng["config"]
    # Strip dangerous overrides
    config_updates.pop("auth", None)
    if isinstance(config_updates.get("pipeline"), dict):
        config_updates["pipeline"].pop("output_dir", None)
    elif "pipeline" in config_updates:
        config_updates.pop("pipeline")
    # Deep merge updates into existing config
    def _deep_merge(base, override):
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                _deep_merge(base[k], v)
            else:
                base[k] = v
    _deep_merge(current_config, config_updates)

    from bug_hunter.core.database import get_db
    import json as _json
    with get_db() as conn:
        conn.execute(
            "UPDATE engagements SET config = ?, updated_at = ? WHERE id = ?",
            (_json.dumps(current_config), __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(), engagement_id),
        )
    return get_engagement(engagement_id)


@router.post("/engagements/{engagement_id}/runs")
async def api_start_run(engagement_id: str, req: StartRunRequest):
    eng = get_engagement(engagement_id)
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")

    active_runs = [r for r in list_runs(engagement_id) if r["status"] == "running"]
    if active_runs:
        raise HTTPException(
            status_code=409,
            detail="A run is already active for this engagement. Wait for it to complete.",
        )

    config = load_config()
    eng_config_data = eng["config"]

    from bug_hunter.core.config import _merge_dict_into_dataclass
    _merge_dict_into_dataclass(config, eng_config_data)

    orchestrator = PipelineOrchestrator(config, engagement_id)
    _orchestrators[engagement_id] = orchestrator

    try:
        run_id = await orchestrator.start_run(
            run_type=req.run_type,
            rehunt_target=req.rehunt_target if req.rehunt_target else None,
            setup_instructions=req.setup_instructions if req.setup_instructions else None,
            bug_ids=req.bug_ids if req.bug_ids else None,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="A run is already active for this engagement. Wait for it to complete.",
        )

    return {"run_id": run_id, "status": "running"}


@router.delete("/engagements/{engagement_id}/runs/{run_id}")
async def api_delete_run(engagement_id: str, run_id: str):
    """Delete a run and all its data."""
    run = _verify_run_ownership(engagement_id, run_id)
    if run["status"] == "running":
        raise HTTPException(status_code=409, detail="Cannot delete a running run — cancel it first")

    from bug_hunter.core.database import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM bugs WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM stage_results WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    # Delete output files
    eng = get_engagement(engagement_id)
    if eng:
        output_dir = _engagement_output_dir(eng)
        run_dir = os.path.join(output_dir, "engagements", engagement_id, "runs", run_id)
        if os.path.isdir(run_dir):
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)

    return {"status": "deleted"}


@router.post("/engagements/{engagement_id}/runs/{run_id}/cancel")
async def api_cancel_run(engagement_id: str, run_id: str):
    _verify_run_ownership(engagement_id, run_id)
    orchestrator = _orchestrators.get(engagement_id)
    if not orchestrator:
        raise HTTPException(status_code=404, detail="No active orchestrator for this engagement")
    cancelled = await orchestrator.cancel_run()
    if not cancelled:
        raise HTTPException(status_code=409, detail="No running pipeline to cancel")
    return {"status": "cancelled", "run_id": run_id}


@router.post("/engagements/{engagement_id}/runs/{run_id}/pause")
async def api_pause_run(engagement_id: str, run_id: str):
    _verify_run_ownership(engagement_id, run_id)
    orchestrator = _orchestrators.get(engagement_id)
    if not orchestrator:
        raise HTTPException(status_code=404, detail="No active orchestrator for this engagement")
    paused = await orchestrator.pause_run()
    if not paused:
        raise HTTPException(status_code=409, detail="No running pipeline to pause")
    return {"status": "pausing", "run_id": run_id}


@router.post("/engagements/{engagement_id}/runs/{run_id}/resume")
async def api_resume_run(engagement_id: str, run_id: str):
    run = _verify_run_ownership(engagement_id, run_id)

    active_runs = [r for r in list_runs(engagement_id) if r["status"] == "running" and r["id"] != run_id]
    if active_runs:
        raise HTTPException(
            status_code=409,
            detail="Another run is already active for this engagement. Wait for it to complete.",
        )

    if run["status"] not in ("paused", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail="Only paused, failed, or cancelled runs can be resumed")

    eng = get_engagement(engagement_id)
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")

    config = load_config()
    from bug_hunter.core.config import _merge_dict_into_dataclass
    _merge_dict_into_dataclass(config, eng["config"])

    orchestrator = PipelineOrchestrator(config, engagement_id)
    _orchestrators[engagement_id] = orchestrator
    try:
        resumed = await orchestrator.resume_run(run_id)
    except Exception as e:
        if "UNIQUE" in str(e) or "IntegrityError" in type(e).__name__:
            raise HTTPException(status_code=409, detail="Another run became active concurrently")
        raise
    if not resumed:
        raise HTTPException(status_code=409, detail="Run could not be resumed")
    return {"status": "running", "run_id": run_id}


@router.get("/engagements/{engagement_id}/runs")
async def api_list_runs(engagement_id: str):
    return list_runs(engagement_id)


@router.get("/engagements/{engagement_id}/report")
async def api_get_report(engagement_id: str):
    """Get the latest summary report markdown."""
    eng = get_engagement(engagement_id)
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")
    output_dir = _engagement_output_dir(eng)
    report_path = os.path.join(output_dir, "engagements", engagement_id, "cumulative", "report.md")
    if not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="No report generated yet")
    with open(report_path) as f:
        return {"content": f.read()}


_report_status: dict[str, dict] = {}


async def _generate_report_async(engagement_id: str, config=None):
    """Reusable report generation — called by API endpoint and auto-trigger."""
    eng = get_engagement(engagement_id)
    if not eng:
        raise ValueError("Engagement not found")

    if config is None:
        from bug_hunter.core.config import _merge_dict_into_dataclass
        config = load_config()
        _merge_dict_into_dataclass(config, eng["config"])

    from bug_hunter.pipeline.stages.summarizer import SummarizerStage
    from bug_hunter.pipeline.stages.base import StageContext

    output_dir = _engagement_output_dir(eng)
    cumulative_dir = os.path.join(output_dir, "engagements", engagement_id, "cumulative")
    os.makedirs(cumulative_dir, exist_ok=True)

    runs = list_runs(engagement_id)
    latest_run = runs[-1] if runs else None
    run_id = latest_run["id"] if latest_run else "manual"
    run_dir = os.path.join(output_dir, "engagements", engagement_id, "runs", run_id) if latest_run else cumulative_dir

    context = StageContext(
        config=config,
        engagement_id=engagement_id,
        engagement=eng,
        run_id=run_id,
        run_dir=run_dir,
        cumulative_dir=cumulative_dir,
    )

    stage = SummarizerStage()
    result = await stage.execute(context)
    if not result.success:
        raise RuntimeError(result.error or "Report generation failed")


@router.post("/engagements/{engagement_id}/report/generate")
async def api_generate_report(engagement_id: str):
    """Generate/regenerate the summary report on demand."""
    eng = get_engagement(engagement_id)
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")

    if _report_status.get(engagement_id, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="Report generation already in progress")

    _report_status[engagement_id] = {"status": "running", "message": "Generating report..."}

    import asyncio

    async def _run():
        try:
            await _generate_report_async(engagement_id)
            _report_status[engagement_id] = {"status": "completed", "message": "Report generated"}
        except Exception as e:
            _report_status[engagement_id] = {"status": "failed", "message": str(e)}

    asyncio.create_task(_run())
    return {"status": "started"}


@router.get("/engagements/{engagement_id}/report/status")
async def api_report_status(engagement_id: str):
    """Check the status of report generation."""
    return _report_status.get(engagement_id, {"status": "idle"})


def _verify_run_ownership(engagement_id: str, run_id: str) -> dict:
    """Verify a run belongs to the given engagement."""
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["engagement_id"] != engagement_id:
        raise HTTPException(status_code=404, detail="Run not found for this engagement")
    return run


@router.get("/engagements/{engagement_id}/runs/{run_id}")
async def api_get_run(engagement_id: str, run_id: str):
    run = _verify_run_ownership(engagement_id, run_id)
    run["stages"] = list_stage_results(run_id)
    return run


@router.get("/engagements/{engagement_id}/runs/{run_id}/events")
async def api_get_run_events(engagement_id: str, run_id: str):
    _verify_run_ownership(engagement_id, run_id)
    from bug_hunter.core.database import list_events
    events = list_events(run_id)
    return {"events": events}


@router.get("/engagements/{engagement_id}/bugs")
async def api_list_bugs(engagement_id: str, status: str = None):
    return list_bugs(engagement_id, status=status)


@router.get("/engagements/{engagement_id}/chains")
async def api_list_chains(engagement_id: str):
    return list_chains(engagement_id)


@router.get("/engagements/{engagement_id}/runs/{run_id}/stages/{stage_name}/output")
async def api_get_stage_output(engagement_id: str, run_id: str, stage_name: str,
                                path: str = Query(default="")):
    """Browse stage output files."""
    _verify_run_ownership(engagement_id, run_id)
    engagement = get_engagement(engagement_id)
    if not engagement:
        raise HTTPException(status_code=404, detail="Engagement not found")

    stages = list_stage_results(run_id)
    stage = next((s for s in stages if s["stage_name"] == stage_name), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Stage not found")

    stage_order = stage["stage_order"]
    stage_dir = os.path.join(
        _engagement_output_dir(engagement), "engagements", engagement_id,
        "runs", run_id, f"{stage_order:02d}_{stage_name}",
    )

    if not os.path.exists(stage_dir):
        return {"files": [], "content": None}

    if path:
        full_path = os.path.join(stage_dir, path)
        if not _is_safe_path(stage_dir, full_path):
            raise HTTPException(status_code=403, detail="Path traversal detected")

        if os.path.isfile(full_path):
            try:
                with open(full_path) as f:
                    content = f.read()
                try:
                    return {"content": json.loads(content), "type": "json"}
                except json.JSONDecodeError:
                    return {"content": content, "type": "text"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
        elif os.path.isdir(full_path):
            return {"files": _list_dir(full_path, stage_dir)}
        else:
            raise HTTPException(status_code=404, detail="File not found")

    return {"files": _list_dir(stage_dir, stage_dir)}


@router.get("/engagements/{engagement_id}/cumulative/{filename}")
async def api_get_cumulative(engagement_id: str, filename: str):
    """Get cumulative engagement files."""
    engagement = get_engagement(engagement_id)
    if not engagement:
        raise HTTPException(status_code=404, detail="Engagement not found")
    cumulative_dir = os.path.join(
        _engagement_output_dir(engagement), "engagements", engagement_id, "cumulative",
    )
    filepath = os.path.join(cumulative_dir, filename)

    if not _is_safe_path(cumulative_dir, filepath):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    if not os.path.exists(filepath):
        return {"content": []}

    with open(filepath) as f:
        raw = f.read()
    try:
        return {"content": json.loads(raw)}
    except (json.JSONDecodeError, ValueError):
        return {"content": raw}


@router.get("/usage")
async def api_get_usage():
    """Get usage stats for Claude Code and Codex CLI."""
    import httpx

    result = {}

    # Claude Code usage
    try:
        creds_path = os.path.expanduser("~/.claude/.credentials.json")
        if os.path.exists(creds_path):
            with open(creds_path) as f:
                creds = json.load(f)
            token = creds.get("claudeAiOauth", {}).get("accessToken", "")
            if token:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://api.anthropic.com/api/oauth/usage",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "anthropic-beta": "oauth-2025-04-20",
                        },
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        result["claude"] = resp.json()
                    else:
                        result["claude"] = {"error": f"HTTP {resp.status_code}"}
            else:
                result["claude"] = {"error": "No access token found"}
        else:
            result["claude"] = {"error": "Credentials file not found"}
    except Exception as e:
        result["claude"] = {"error": str(e)}

    # Codex CLI usage
    try:
        auth_path = os.path.expanduser("~/.codex/auth.json")
        if os.path.exists(auth_path):
            with open(auth_path) as f:
                auth = json.load(f)
            token = auth.get("tokens", {}).get("access_token", "")
            if token:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://chatgpt.com/backend-api/wham/usage",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        result["codex"] = resp.json()
                    else:
                        result["codex"] = {"error": f"HTTP {resp.status_code}"}
            else:
                result["codex"] = {"error": "No access token found"}
        else:
            result["codex"] = {"error": "Credentials file not found"}
    except Exception as e:
        result["codex"] = {"error": str(e)}

    return result


@router.get("/settings")
async def api_get_settings():
    """Get the current global configuration."""
    config = load_config()
    full = config_to_dict(config)
    # Don't send auth config to the frontend
    full.pop("auth", None)
    full.pop("engagement", None)
    return full


@router.put("/settings")
async def api_update_settings(settings: dict):
    """Update global configuration. Merges with existing config."""
    config = load_config()
    # Don't allow overriding auth or engagement defaults via settings
    settings.pop("auth", None)
    settings.pop("engagement", None)
    from bug_hunter.core.config import _merge_dict_into_dataclass, save_config
    _merge_dict_into_dataclass(config, settings)
    save_config(config, DEFAULT_CONFIG_PATH)
    full = config_to_dict(config)
    full.pop("auth", None)
    full.pop("engagement", None)
    return full


def _is_safe_path(base_dir: str, requested_path: str) -> bool:
    """Check that requested_path is inside base_dir, preventing traversal."""
    base = os.path.realpath(base_dir)
    target = os.path.realpath(requested_path)
    try:
        return os.path.commonpath([base, target]) == base
    except ValueError:
        return False


def _list_dir(dir_path: str, base_path: str) -> list[dict]:
    """List directory contents with metadata."""
    items = []
    for entry in sorted(os.scandir(dir_path), key=lambda e: e.name):
        rel_path = os.path.relpath(entry.path, base_path)
        item = {
            "name": entry.name,
            "path": rel_path,
            "is_dir": entry.is_dir(),
        }
        if entry.is_file():
            item["size"] = entry.stat().st_size
        items.append(item)
    return items


def _deep_merge(base: dict, override: dict):
    """Deep merge override into base."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
