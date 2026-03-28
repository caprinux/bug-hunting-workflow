"""Base class for pipeline stages."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from bug_hunter.core.config import AppConfig


@dataclass
class StageContext:
    """Context passed to each pipeline stage."""
    config: AppConfig
    engagement_id: str
    engagement: dict
    run_id: str
    run_dir: str
    cumulative_dir: str
    run_type: str = "initial"
    rehunt_target: Optional[str] = None


@dataclass
class StageResult:
    """Result returned by a pipeline stage."""
    success: bool
    error: str = ""
    input_count: int = 0
    output_count: int = 0
    cost_usd: float = 0.0
    metadata: Optional[dict] = None


class PipelineStage(ABC):
    """Base class for all pipeline stages."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stage name matching the stage registry key."""
        ...

    @abstractmethod
    async def execute(self, context: StageContext) -> StageResult:
        """Execute this pipeline stage."""
        ...

    def get_stage_dir(self, context: StageContext) -> str:
        """Get the output directory for this stage."""
        import os
        stage_order = self._get_stage_order(context)
        dir_name = f"{stage_order:02d}_{self.name}"
        path = os.path.join(context.run_dir, dir_name)
        os.makedirs(path, exist_ok=True)
        return path

    def _get_stage_order(self, context: StageContext) -> int:
        """Get the numeric order for this stage."""
        for name, order in self._get_stage_lookup(context):
            if name == self.name:
                return order
        return 99

    def _stage_output_path(self, context: StageContext, stage_name: str,
                            filename: str) -> str:
        """Get the absolute file path for a previous stage's output."""
        import os

        order = self._resolve_stage_order(context, stage_name)
        dir_name = f"{order:02d}_{stage_name}"
        return os.path.abspath(os.path.join(context.run_dir, dir_name, filename))

    @staticmethod
    def _get_stage_lookup(context: StageContext) -> list:
        """Get the stage list appropriate for the current run type."""
        from bug_hunter.pipeline.orchestrator import PIPELINE_STAGES, REVALIDATION_STAGES
        if context.run_type == "revalidation":
            return REVALIDATION_STAGES
        return PIPELINE_STAGES

    @staticmethod
    def _resolve_stage_order(context: StageContext, stage_name: str) -> int:
        """Resolve the numeric order for a stage, falling back to PIPELINE_STAGES
        for copied prerequisite stages (e.g. scoper/setup in revalidation runs)."""
        from bug_hunter.pipeline.orchestrator import PIPELINE_STAGES, REVALIDATION_STAGES
        lookup = REVALIDATION_STAGES if context.run_type == "revalidation" else PIPELINE_STAGES
        for name, order in lookup:
            if name == stage_name:
                return order
        # Fall back to main pipeline for copied stages
        for name, order in PIPELINE_STAGES:
            if name == stage_name:
                return order
        return 99

    def read_previous_output(self, context: StageContext, stage_name: str,
                             filename: str) -> Any:
        """Read output from a previous stage."""
        import json
        import os
        filepath = self._stage_output_path(context, stage_name, filename)
        if os.path.exists(filepath):
            with open(filepath) as f:
                return json.load(f)
        return None

    def write_output(self, context: StageContext, filename: str, data: Any):
        """Write output to this stage's directory."""
        import json
        import os
        stage_dir = self.get_stage_dir(context)
        filepath = os.path.join(stage_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    def prepare_agent_run(self, context: StageContext, agent_name: str, label: str,
                          metadata: Optional[dict] = None) -> tuple[str, dict]:
        """Create a per-invocation log directory and append a stage index entry."""
        import json
        import os
        import re
        from datetime import datetime, timezone
        from uuid import uuid4

        def _slug(value: str) -> str:
            slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("._-")
            return slug or "run"

        created_at = datetime.now(timezone.utc).isoformat()
        invocation_id = (
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
            f"_{_slug(agent_name)}_{_slug(label)}_{uuid4().hex[:8]}"
        )

        runs_dir = os.path.join(self.get_stage_dir(context), "agent_runs")
        os.makedirs(runs_dir, exist_ok=True)

        record_dir = os.path.join(runs_dir, invocation_id)
        os.makedirs(record_dir, exist_ok=True)

        index_entry = {
            "invocation_id": invocation_id,
            "created_at": created_at,
            "stage": self.name,
            "engagement_id": context.engagement_id,
            "run_id": context.run_id,
            "agent": agent_name,
            "label": label,
        }
        if metadata:
            index_entry["metadata"] = metadata

        with open(os.path.join(runs_dir, "index.jsonl"), "a") as f:
            json.dump(index_entry, f)
            f.write("\n")

        return record_dir, index_entry
