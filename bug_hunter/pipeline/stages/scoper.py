"""Scoper stage — understand the target, map attack surfaces, identify scope."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from bug_hunter.core.cli_wrapper import run_agent
from bug_hunter.core.events import event_manager
from bug_hunter.utils.result_parser import parse_agent_result
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = Path(__file__).parent.parent.parent.parent / "agents"
SCHEMAS_DIR = Path(__file__).parent.parent.parent.parent / "schemas"


@register
class ScoperStage(PipelineStage):

    @property
    def name(self) -> str:
        return "scoper"

    async def execute(self, context: StageContext) -> StageResult:
        stage_dir = self.get_stage_dir(context)
        eng_config = context.engagement["config"]
        eng_type = context.engagement["type"]
        scope_def = eng_config.get("engagement", {}).get("scope_definition", "")

        # Get source path from setup
        setup_data = self.read_previous_output(context, "setup", "setup.json")
        source_path = ""
        if setup_data and "source" in setup_data:
            source_path = setup_data["source"]["local_path"]
        if not source_path:
            source_path = eng_config.get("engagement", {}).get("source_path", "")

        if eng_type == "source_code" and not source_path:
            return StageResult(success=False, error="No source path available")

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            "Mapping target architecture and attack surfaces...",
        )

        # Build available tools list from setup stage
        setup_data = self.read_previous_output(context, "setup", "setup.json")
        available_tools = ""
        if setup_data and "tools" in setup_data:
            tool_list = [
                f"{t['name']} ({t['path']})" for t in setup_data["tools"]
                if t.get("available") and t["name"] not in ("claude", "codex", "git", "python3", "pip3", "curl")
            ]
            if tool_list:
                available_tools = "AVAILABLE TOOLS: " + ", ".join(tool_list)

        if eng_type == "source_code":
            prompt = self._build_source_code_prompt(source_path, scope_def)
            agent_file = str(AGENTS_DIR / "source_code" / "scoper.md")
            cwd = source_path
        else:
            stage_dir = self.get_stage_dir(context)
            domains = eng_config.get("engagement", {}).get("target_domains", [])
            infra_config = eng_config.get("engagement", {}).get("infra_config", "")
            prompt = self._build_black_box_prompt(domains, scope_def, infra_config, available_tools)
            agent_file = str(AGENTS_DIR / "black_box" / "scoper.md")
            cwd = stage_dir

        record_dir, record_meta = self.prepare_agent_run(
            context, self._agent_name_for_model(context.config.models.scoper), "scoper",
            {"model": context.config.models.scoper, "engagement_type": eng_type},
        )

        result = await run_agent(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.scoper,
            cwd=cwd,
            timeout=context.config.pipeline.subagent_timeout,
            record_dir=record_dir,
            record_metadata=record_meta,
            json_schema_file=str(SCHEMAS_DIR / "scoper.json"),
        )

        if not result.success:
            return StageResult(success=False, error=result.error, cost_usd=result.cost_usd)

        scope_data = parse_agent_result(result.result, ['attack_surfaces', 'architecture'], "scoper")

        # Fallback: if agent wrote output to a file instead of returning JSON
        if not scope_data.get("attack_surfaces"):
            import glob
            stage_dir = self.get_stage_dir(context)
            for fallback in glob.glob(os.path.join(stage_dir, "**", "*.json"), recursive=True):
                if "scope" in os.path.basename(fallback).lower() and fallback != os.path.join(stage_dir, "scope.json"):
                    try:
                        with open(fallback) as fb:
                            candidate = json.load(fb)
                        if isinstance(candidate, dict) and candidate.get("attack_surfaces"):
                            logger.info(f"Recovered scope data from agent-written file: {fallback}")
                            scope_data = candidate
                            break
                    except (json.JSONDecodeError, IOError):
                        continue

        self.write_output(context, "scope.json", scope_data)

        attack_surfaces = scope_data.get("attack_surfaces", [])
        return StageResult(
            success=True,
            output_count=len(attack_surfaces),
            cost_usd=result.cost_usd,
            metadata={"attack_surfaces": len(attack_surfaces)},
        )

    def _build_source_code_prompt(self, source_path: str, scope_def: str) -> str:
        return f"""SOURCE CODE ROOT: {source_path}

SCOPE DEFINITION:
{scope_def or "All code is in scope. Focus on security-relevant functionality."}

Your output will be collected automatically via structured JSON output. Do not write results to any file."""

    def _build_black_box_prompt(self, domains: list, scope_def: str, infra_config: str,
                                available_tools: str = "") -> str:
        return f"""TARGET DOMAINS: {json.dumps(domains)}

SCOPE DEFINITION:
{scope_def or "All domains listed are in scope."}

INFRASTRUCTURE ACCESS:
{infra_config}

{available_tools}

Your output will be collected automatically via structured JSON output. Do not write results to any file."""
