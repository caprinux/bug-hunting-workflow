"""De-duplicator stage — merge duplicate findings from multiple agents."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from bug_hunter.core.cli_wrapper import run_agent
from bug_hunter.core.database import list_bugs, update_bug
from bug_hunter.core.events import event_manager
from bug_hunter.utils.result_parser import parse_agent_result
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents")
SCHEMAS_DIR = Path(__file__).parent.parent.parent.parent / "schemas"


@register
class DeduplicatorStage(PipelineStage):

    @property
    def name(self) -> str:
        return "deduplicator"

    async def execute(self, context: StageContext) -> StageResult:
        bugs = list_bugs(context.engagement_id, status="found", run_id=context.run_id)
        if not bugs:
            self.write_output(context, "deduplicated_findings.json", [])
            self.write_output(context, "duplicate_groups.json", [])
            return StageResult(success=True, input_count=0, output_count=0)

        bug_data_list = [b["bug_data"] for b in bugs]

        # Collect existing bugs from prior runs (confirmed, cannot_validate, etc.)
        # The deduplicator should discard new findings that duplicate these
        all_existing = list_bugs(context.engagement_id)
        existing_bugs = [
            b["bug_data"] for b in all_existing
            if b["run_id"] != context.run_id and b["status"] not in ("discarded", "out_of_scope")
        ]

        stage_dir = self.get_stage_dir(context)

        # Write new findings
        findings_file = os.path.join(stage_dir, "input_findings.json")
        with open(findings_file, "w") as f:
            json.dump(bug_data_list, f, indent=2)
        findings_path = os.path.abspath(findings_file)

        # Write existing bugs for reference
        existing_section = ""
        if existing_bugs:
            existing_file = os.path.join(stage_dir, "existing_bugs.json")
            with open(existing_file, "w") as f:
                json.dump(existing_bugs, f, indent=2)
            existing_path = os.path.abspath(existing_file)
            existing_section = f"\nEXISTING BUGS FROM PRIOR RUNS ({len(existing_bugs)} total): Read {existing_path}\nDo NOT remove or modify these. Only discard NEW findings that duplicate an existing bug."

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"De-duplicating {len(bug_data_list)} findings against {len(existing_bugs)} existing bugs",
        )

        prompt = f"""You are de-duplicating security vulnerability findings that may have been flagged by multiple agents.

NEW FINDINGS ({len(bug_data_list)} total): Read {findings_path}
{existing_section}

RULES:
1. Same file and line range (source code) OR same URL and parameter (black box) = obvious duplicate → merge
2. Overlapping line ranges or same endpoint with similar payload = likely duplicate → merge
3. Different code paths leading to the same vulnerable sink = semantic duplicate → merge
4. Same vulnerability PATTERN at genuinely DIFFERENT locations = NOT duplicates, preserve as distinct bugs
5. When merging, combine reasoning from all agents. Note which agents agreed (multi-agent agreement = higher confidence).
6. Preserve the most specific/detailed version of each field.
7. If a new finding duplicates an EXISTING bug from a prior run, discard the new finding (mark it as a duplicate of the existing bug ID).

Your output will be collected automatically via structured JSON output. Do not write results to any file."""

        agent_file = os.path.join(AGENTS_DIR, "shared", "deduplicator.md")
        record_dir, record_metadata = self.prepare_agent_run(
            context, self._agent_name_for_model(context.config.models.deduplicator),
            "deduplicate_findings",
            {"model": context.config.models.deduplicator, "finding_count": len(bug_data_list)},
        )

        result = await run_agent(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.deduplicator,
            timeout=context.config.pipeline.subagent_timeout,
            record_dir=record_dir,
            record_metadata=record_metadata,
            json_schema_file=str(SCHEMAS_DIR / "deduplicator.json"),
        )

        if not result.success:
            self.write_output(context, "deduplicated_findings.json", bug_data_list)
            self.write_output(context, "duplicate_groups.json", [])
            return StageResult(
                success=True,
                input_count=len(bug_data_list),
                output_count=len(bug_data_list),
                cost_usd=result.cost_usd,
                metadata={"dedup_failed": True, "error": result.error},
            )

        dedup_result = parse_agent_result(result.result, ['deduplicated', 'duplicate_groups'], "deduplicator")
        deduplicated = dedup_result.get("deduplicated", bug_data_list)
        groups = dedup_result.get("duplicate_groups", [])

        self.write_output(context, "deduplicated_findings.json", deduplicated)
        self.write_output(context, "duplicate_groups.json", groups)

        merged_ids = set()
        for group in groups:
            for dup_id in group.get("duplicates", []):
                merged_ids.add(dup_id)

        # Update surviving bugs with merged data from the LLM (combined reasoning, found_by, etc.)
        dedup_by_id = {d.get("id"): d for d in deduplicated if isinstance(d, dict) and d.get("id")}
        for bug in bugs:
            bug_ext_id = bug["bug_data"].get("id")
            if bug_ext_id in merged_ids:
                update_bug(bug["id"], status="discarded")
            elif bug_ext_id in dedup_by_id:
                update_bug(bug["id"], bug_data=dedup_by_id[bug_ext_id])

        return StageResult(
            success=True,
            input_count=len(bug_data_list),
            output_count=len(deduplicated),
            cost_usd=result.cost_usd,
            metadata={"duplicates_removed": len(merged_ids)},
        )
