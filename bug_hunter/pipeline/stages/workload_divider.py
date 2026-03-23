"""Workload Divider stage — split massive codebases into independent subsystems."""

from __future__ import annotations

import json
import logging
import os

from bug_hunter.core.cli_wrapper import run_claude
from bug_hunter.core.events import event_manager
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents")


@register
class WorkloadDividerStage(PipelineStage):

    @property
    def name(self) -> str:
        return "workload_divider"

    async def execute(self, context: StageContext) -> StageResult:
        if not context.config.workload_divider.enabled:
            return StageResult(success=True, metadata={"skipped": "not enabled"})

        eng_config = context.engagement["config"]
        setup_data = self.read_previous_output(context, "setup", "setup.json")
        source_path = ""
        if setup_data and "source" in setup_data:
            source_path = setup_data["source"]["local_path"]
        if not source_path:
            source_path = eng_config.get("engagement", {}).get("source_path", "")

        strategy = context.config.workload_divider.subsystem_strategy
        manual = context.config.workload_divider.manual_subsystems

        if strategy == "manual" and manual:
            subsystems = [{"path": p, "shared_context": []} for p in manual]
            self.write_output(context, "subsystems.json", {"subsystems": subsystems})
            return StageResult(success=True, output_count=len(subsystems))

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Analyzing codebase structure at: {source_path}",
        )

        top_dirs = []
        try:
            for entry in os.scandir(source_path):
                if entry.is_dir() and not entry.name.startswith("."):
                    file_count = sum(1 for _, _, files in os.walk(entry.path) for _ in files)
                    top_dirs.append({"name": entry.name, "path": entry.path, "file_count": file_count})
        except OSError as e:
            return StageResult(success=False, error=f"Cannot scan directory: {e}")

        prompt = f"""Analyze this codebase structure and split it into independent subsystems for parallel auditing.

SOURCE ROOT: {source_path}

TOP-LEVEL DIRECTORIES:
{json.dumps(top_dirs, indent=2)}

INSTRUCTIONS:
1. Group directories into independent subsystems that can be audited separately
2. Identify cross-subsystem interfaces (shared headers, common APIs, middleware)
3. These interfaces should be included as shared context for each subsystem's auditor

Output a JSON object:
{{
  "subsystems": [
    {{
      "name": "networking",
      "paths": ["net/", "drivers/net/"],
      "description": "Network stack and drivers",
      "shared_context": ["include/net/", "include/linux/socket.h"]
    }}
  ]
}}"""

        agent_file = os.path.join(AGENTS_DIR, "source_code", "workload_divider.md")

        result = await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.workload_divider,
            timeout=context.config.pipeline.subagent_timeout,
        )

        if not result.success:
            subsystems = [{"path": d["path"], "name": d["name"], "shared_context": []} for d in top_dirs[:20]]
            self.write_output(context, "subsystems.json", {"subsystems": subsystems})
            return StageResult(success=True, output_count=len(subsystems), cost_usd=result.cost_usd)

        subsystem_data = result.result or {"subsystems": []}
        self.write_output(context, "subsystems.json", subsystem_data)

        return StageResult(
            success=True,
            output_count=len(subsystem_data.get("subsystems", [])),
            cost_usd=result.cost_usd,
        )
