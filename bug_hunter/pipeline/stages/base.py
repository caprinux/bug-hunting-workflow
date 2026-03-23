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
        from bug_hunter.pipeline.orchestrator import SOURCE_CODE_STAGES, BLACK_BOX_STAGES
        stages = SOURCE_CODE_STAGES if context.engagement["type"] == "source_code" else BLACK_BOX_STAGES
        for name, order in stages:
            if name == self.name:
                return order
        return 99

    def read_previous_output(self, context: StageContext, stage_name: str,
                             filename: str) -> Any:
        """Read output from a previous stage."""
        import json
        import os
        from bug_hunter.pipeline.orchestrator import SOURCE_CODE_STAGES, BLACK_BOX_STAGES
        stages = SOURCE_CODE_STAGES if context.engagement["type"] == "source_code" else BLACK_BOX_STAGES

        order = 0
        for name, o in stages:
            if name == stage_name:
                order = o
                break

        dir_name = f"{order:02d}_{stage_name}"
        filepath = os.path.join(context.run_dir, dir_name, filename)
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
