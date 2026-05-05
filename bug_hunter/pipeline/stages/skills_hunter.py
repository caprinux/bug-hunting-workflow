"""Skills Hunter stage — automated security scanning using semgrep, insecure defaults, and supply chain audit."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from bug_hunter.core.cli_wrapper import run_agent
from bug_hunter.core.database import create_bug
from bug_hunter.core.events import event_manager
from bug_hunter.utils.result_parser import parse_agent_result
from bug_hunter.utils.schema_validator import validate_findings_list
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = Path(__file__).parent.parent.parent.parent / "agents"
SCHEMAS_DIR = Path(__file__).parent.parent.parent.parent / "schemas"


@register
class SkillsHunterStage(PipelineStage):

    @property
    def name(self) -> str:
        return "skills_hunter"

    async def execute(self, context: StageContext) -> StageResult:
        eng_type = context.engagement["type"]

        # Source-code only stage
        if eng_type != "source_code":
            return StageResult(success=True, input_count=0, output_count=0,
                               metadata={"skipped": "black_box_engagement"})

        stage_dir = self.get_stage_dir(context)
        eng_config = context.engagement["config"]

        # Get source path from setup
        setup_data = self.read_previous_output(context, "setup", "setup.json")
        source_path = ""
        if setup_data and "source" in setup_data:
            source_path = setup_data["source"]["local_path"]
        if not source_path:
            source_path = eng_config.get("engagement", {}).get("source_path", "")

        if not source_path:
            return StageResult(success=False, error="No source path available")

        scope_file = self._stage_output_path(context, "scoper", "scope.json")
        scope_line = f"APPLICATION CONTEXT: Read {scope_file}" if os.path.exists(scope_file) else ""

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            "Running automated security scans (semgrep, insecure defaults, supply chain)...",
        )

        agent_file = str(AGENTS_DIR / "source_code" / "skills_hunter.md")
        schema_file = str(SCHEMAS_DIR / "bug_hunter.json")

        prompt = f"""SOURCE CODE ROOT: {source_path}
{scope_line}

Run the following three security scans against the codebase, then report all findings.

1. SEMGREP SCAN: Detect languages present, run semgrep with appropriate security rulesets per language. Use --metrics=off --json. Parse the output and convert each finding into a bug.

2. INSECURE DEFAULTS: Search for hardcoded secrets, default credentials, weak crypto configurations, fail-open security configs, debug flags enabled.

3. SUPPLY CHAIN: Analyze dependency manifests (package.json, requirements.txt, Gemfile, go.mod, pom.xml, Cargo.toml, etc.) for unpinned deps, known risky packages, missing lockfiles.

Your output will be collected automatically via structured JSON output. Do not write results to any file."""

        record_dir, record_meta = self.prepare_agent_run(
            context, self._agent_name_for_model(context.config.models.skills_hunter), "skills_hunt",
            {"model": context.config.models.skills_hunter, "engagement_type": eng_type},
        )

        result = await run_agent(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.skills_hunter,
            cwd=source_path,
            json_schema_file=schema_file,
            timeout=context.config.pipeline.subagent_timeout,
            record_dir=record_dir,
            record_metadata=record_meta,
            reasoning_effort=context.config.pipeline.codex_reasoning_effort,
            reasoning_summary=context.config.pipeline.codex_reasoning_summary,
        )

        if not result.success:
            return StageResult(success=False, error=result.error, cost_usd=result.cost_usd)

        data = parse_agent_result(
            result.result, ["bugs", "attack_surfaces"], "skills_hunter",
            save_raw_to=os.path.join(stage_dir, "raw_output.md"),
        )

        new_bugs = data.get("bugs", [])

        run_prefix = context.run_id[:8]
        for i, bug in enumerate(new_bugs):
            bug["id"] = f"{run_prefix}/skills-{i:03d}"
            bug["found_by"] = ["skills_hunter"]

        quarantine_dir = os.path.join(stage_dir, "quarantined")
        valid_bugs, quarantined = validate_findings_list(new_bugs, quarantine_dir)

        self.write_output(context, "all_findings.json", valid_bugs)

        for bug in valid_bugs:
            create_bug(context.engagement_id, context.run_id, bug)

        return StageResult(
            success=True,
            input_count=0,
            output_count=len(valid_bugs),
            cost_usd=result.cost_usd,
            metadata={
                "bugs_found": len(valid_bugs),
                "quarantined": len(quarantined),
            },
        )
