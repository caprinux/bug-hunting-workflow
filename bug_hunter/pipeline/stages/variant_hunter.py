"""Variant Hunter stage — find additional instances of discovered bug patterns."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from bug_hunter.core.cli_wrapper import run_agent
from bug_hunter.core.database import create_bug, list_bugs
from bug_hunter.core.events import event_manager
from bug_hunter.utils.result_parser import parse_agent_result
from bug_hunter.utils.schema_validator import validate_findings_list
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = Path(__file__).parent.parent.parent.parent / "agents"
SCHEMAS_DIR = Path(__file__).parent.parent.parent.parent / "schemas"


@register
class VariantHunterStage(PipelineStage):

    @property
    def name(self) -> str:
        return "variant_hunter"

    async def execute(self, context: StageContext) -> StageResult:
        stage_dir = self.get_stage_dir(context)
        eng_config = context.engagement["config"]

        # Collect all bugs found so far — from bug_hunter BUGS.json + skills_hunter + DB
        bugs_data = self.read_previous_output(context, "bug_hunter", "BUGS.json") or []
        skills_findings = self.read_previous_output(context, "skills_hunter", "all_findings.json") or []
        all_bugs = bugs_data + skills_findings

        # Also include bugs from DB (previous runs on rehunts)
        if context.run_type == "rehunt":
            db_bugs = list_bugs(context.engagement_id, status="found", run_id=context.run_id)
            db_bug_data = [b["bug_data"] for b in db_bugs if b.get("bug_data")]
            all_bugs.extend(db_bug_data)

        # Filter to patternable bugs (have source_file and vuln_type)
        patternable = [b for b in all_bugs
                       if isinstance(b, dict) and b.get("source_file") and b.get("vuln_type")]

        if not patternable:
            self.write_output(context, "variant_findings.json", [])
            return StageResult(success=True, input_count=0, output_count=0,
                               metadata={"skipped": "no_patternable_bugs"})

        # Get source path
        setup_data = self.read_previous_output(context, "setup", "setup.json")
        source_path = ""
        if setup_data and "source" in setup_data:
            source_path = setup_data["source"]["local_path"]
        if not source_path:
            source_path = eng_config.get("engagement", {}).get("source_path", "")

        if not source_path:
            return StageResult(success=False, error="No source path available")

        # Write patterns to file for the agent
        patterns_file = os.path.join(stage_dir, "input_patterns.json")
        with open(patterns_file, "w") as f:
            json.dump(patternable, f, indent=2)
        patterns_path = os.path.abspath(patterns_file)

        scope_file = self._stage_output_path(context, "scoper", "scope.json")

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Searching for variants of {len(patternable)} bug patterns...",
        )

        agent_file = str(AGENTS_DIR / "source_code" / "variant_hunter.md")
        schema_file = str(SCHEMAS_DIR / "bug_hunter.json")

        prompt = f"""SOURCE CODE ROOT: {source_path}
APPLICATION CONTEXT: Read {scope_file}
BUG PATTERNS TO SEARCH FOR: Read {patterns_path}

For each bug, read the original vulnerable code, extract the pattern, and search the rest of the codebase for similar instances. Report only NEW instances at DIFFERENT locations.

Your output will be collected automatically via structured JSON output. Do not write results to any file."""

        record_dir, record_meta = self.prepare_agent_run(
            context, "claude", "variant_hunt",
            {"model": context.config.models.variant_hunter,
             "pattern_count": len(patternable)},
        )

        result = await run_agent(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.variant_hunter,
            cwd=source_path,
            json_schema_file=schema_file,
            timeout=context.config.pipeline.subagent_timeout,
            record_dir=record_dir,
            record_metadata=record_meta,
        )

        if not result.success:
            self.write_output(context, "variant_findings.json", [])
            return StageResult(
                success=True,  # Don't fail pipeline for optional stage
                input_count=len(patternable),
                output_count=0,
                cost_usd=result.cost_usd,
                metadata={"variant_hunt_failed": True, "error": result.error[:200]},
            )

        data = parse_agent_result(
            result.result, ["bugs", "attack_surfaces"], "variant_hunter",
            save_raw_to=os.path.join(stage_dir, "raw_output.md"),
        )

        new_bugs = data.get("bugs", [])

        run_prefix = context.run_id[:8]
        for i, bug in enumerate(new_bugs):
            bug["id"] = f"{run_prefix}/variant-{i:03d}"
            bug["found_by"] = ["variant_hunter"]

        quarantine_dir = os.path.join(stage_dir, "quarantined")
        valid_bugs, quarantined = validate_findings_list(new_bugs, quarantine_dir)

        self.write_output(context, "variant_findings.json", valid_bugs)

        for bug in valid_bugs:
            create_bug(context.engagement_id, context.run_id, bug)

        return StageResult(
            success=True,
            input_count=len(patternable),
            output_count=len(valid_bugs),
            cost_usd=result.cost_usd,
            metadata={
                "patterns_searched": len(patternable),
                "variants_found": len(valid_bugs),
                "quarantined": len(quarantined),
            },
        )
