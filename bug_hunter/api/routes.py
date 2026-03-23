"""FastAPI REST API routes."""

from __future__ import annotations

import json
import os
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

    run_id = await orchestrator.start_run(
        run_type=req.run_type,
        rehunt_target=req.rehunt_target if req.rehunt_target else None,
    )

    return {"run_id": run_id, "status": "running"}


@router.get("/engagements/{engagement_id}/runs")
async def api_list_runs(engagement_id: str):
    return list_runs(engagement_id)


@router.get("/engagements/{engagement_id}/runs/{run_id}")
async def api_get_run(engagement_id: str, run_id: str):
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    run["stages"] = list_stage_results(run_id)
    return run


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
    eng = get_engagement(engagement_id)
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")

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
        if not os.path.abspath(full_path).startswith(os.path.abspath(stage_dir)):
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

    if not os.path.abspath(filepath).startswith(os.path.abspath(cumulative_dir)):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    if not os.path.exists(filepath):
        return {"content": []}

    with open(filepath) as f:
        return {"content": json.loads(f.read())}


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
