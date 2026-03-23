"""FastAPI REST API routes."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from bug_hunter.core.config import AppConfig, config_to_dict, load_config
from bug_hunter.core.database import (
    create_engagement, get_engagement, list_engagements, update_engagement,
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


@router.get("/engagements/{engagement_id}")
async def api_get_engagement(engagement_id: str):
    eng = get_engagement(engagement_id)
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")
    eng["runs"] = list_runs(engagement_id)
    return eng


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
    config = load_config()
    _verify_run_ownership(engagement_id, run_id)

    stages = list_stage_results(run_id)
    stage = next((s for s in stages if s["stage_name"] == stage_name), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Stage not found")

    stage_order = stage["stage_order"]
    stage_dir = os.path.join(
        config.pipeline.output_dir, "engagements", engagement_id,
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
    config = load_config()
    cumulative_dir = os.path.join(
        config.pipeline.output_dir, "engagements", engagement_id, "cumulative",
    )
    filepath = os.path.join(cumulative_dir, filename)

    if not _is_safe_path(cumulative_dir, filepath):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    if not os.path.exists(filepath):
        return {"content": []}

    with open(filepath) as f:
        return {"content": json.loads(f.read())}


CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml")


@router.get("/settings")
async def api_get_settings():
    """Get the current global configuration."""
    config = load_config(CONFIG_FILE if os.path.exists(CONFIG_FILE) else None)
    full = config_to_dict(config)
    # Don't send auth config to the frontend
    full.pop("auth", None)
    full.pop("engagement", None)
    return full


@router.put("/settings")
async def api_update_settings(settings: dict):
    """Update global configuration. Merges with existing config."""
    config = load_config(CONFIG_FILE if os.path.exists(CONFIG_FILE) else None)
    # Don't allow overriding auth or engagement defaults via settings
    settings.pop("auth", None)
    settings.pop("engagement", None)
    from bug_hunter.core.config import _merge_dict_into_dataclass, save_config
    _merge_dict_into_dataclass(config, settings)
    save_config(config, CONFIG_FILE)
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
