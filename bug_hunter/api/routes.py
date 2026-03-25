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
        confirmed = [b for b in all_bugs if b["status"] == "confirmed"]
        eng["bug_counts"] = {
            "total": len(all_bugs),
            "confirmed": len(confirmed),
            "cannot_validate": sum(1 for b in all_bugs if b["status"] == "cannot_validate"),
        }
        # Severity breakdown of confirmed bugs
        sev = {}
        for b in confirmed:
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

    if req.config_overrides:
        _deep_merge(eng_config, req.config_overrides)

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
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="A run is already active for this engagement. Wait for it to complete.",
        )

    return {"run_id": run_id, "status": "running"}


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
    resumed = await orchestrator.resume_run(run_id)
    if not resumed:
        raise HTTPException(status_code=409, detail="Run could not be resumed")
    return {"status": "running", "run_id": run_id}


@router.get("/engagements/{engagement_id}/runs")
async def api_list_runs(engagement_id: str):
    return list_runs(engagement_id)


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
        return {"content": json.loads(f.read())}


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
