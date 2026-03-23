"""Pydantic models for API request/response validation."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class CreateEngagementRequest(BaseModel):
    name: str
    type: str = Field(pattern="^(source_code|black_box)$")
    source_path: str = ""
    source_repo: str = ""
    target_domains: list[str] = []
    scope_definition: str = ""
    infra_config: str = ""
    config_overrides: dict[str, Any] = {}


class StartRunRequest(BaseModel):
    run_type: str = Field(default="initial", pattern="^(initial|rehunt)$")
    rehunt_target: str = ""


class EngagementResponse(BaseModel):
    id: str
    name: str
    type: str
    status: str
    config: dict[str, Any]
    created_at: str
    updated_at: str
    cost_total_usd: float
    runs: list[dict[str, Any]] = []


class RunResponse(BaseModel):
    id: str
    engagement_id: str
    run_number: int
    status: str
    run_type: str
    rehunt_target: Optional[str] = None
    current_stage: Optional[str] = None
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None
    cost_usd: float
    stages: list[dict[str, Any]] = []


class BugResponse(BaseModel):
    id: str
    engagement_id: str
    run_id: str
    bug_data: dict[str, Any]
    status: str
    current_stage: Optional[str] = None
    created_at: str
    updated_at: str


class ChainResponse(BaseModel):
    id: str
    engagement_id: str
    chain_data: dict[str, Any]
    created_at: str
    updated_at: str


class StageOutputRequest(BaseModel):
    """Request to browse stage output files."""
    path: str = ""


class WebSocketMessage(BaseModel):
    type: str  # stage_update, progress, error, completion, log
    engagement_id: str
    run_id: str = ""
    stage: str = ""
    data: dict[str, Any] = {}
