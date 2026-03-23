"""De-duplicator stage — merge duplicate findings from multiple agents."""

from __future__ import annotations

import json
import logging
import os

from bug_hunter.core.cli_wrapper import run_claude
from bug_hunter.core.database import list_bugs, update_bug
from bug_hunter.core.events import event_manager
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents")


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

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"De-duplicating {len(bug_data_list)} findings",
        )

        findings_json = json.dumps(bug_data_list, indent=2)

        prompt = f"""You are de-duplicating security vulnerability findings that may have been flagged by multiple agents.

FINDINGS ({len(bug_data_list)} total):
{findings_json[:80000]}

RULES:
1. Same file and line range (source code) OR same URL and parameter (black box) = obvious duplicate → merge
2. Overlapping line ranges or same endpoint with similar payload = likely duplicate → merge
3. Different code paths leading to the same vulnerable sink = semantic duplicate → merge
4. Same vulnerability PATTERN at genuinely DIFFERENT locations = NOT duplicates, preserve as distinct bugs
5. When merging, combine reasoning from all agents. Note which agents agreed (multi-agent agreement = higher confidence).
6. Preserve the most specific/detailed version of each field.

Output a JSON object with:
{{
  "deduplicated": [
    // merged findings with combined reasoning and found_by lists
  ],
  "duplicate_groups": [
    {{
      "merged_into": "bug-id",
      "duplicates": ["bug-id-1", "bug-id-2"],
      "reason": "same vulnerable SQL query at line 45"
    }}
  ]
}}"""

        agent_file = os.path.join(AGENTS_DIR, "shared", "deduplicator.md")

        result = await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.deduplicator,
            timeout=context.config.pipeline.subagent_timeout,
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

        dedup_result = result.result or {}
        deduplicated = dedup_result.get("deduplicated", bug_data_list)
        groups = dedup_result.get("duplicate_groups", [])

        self.write_output(context, "deduplicated_findings.json", deduplicated)
        self.write_output(context, "duplicate_groups.json", groups)

        merged_ids = set()
        for group in groups:
            for dup_id in group.get("duplicates", []):
                merged_ids.add(dup_id)

        for bug in bugs:
            if bug["bug_data"].get("id") in merged_ids:
                update_bug(bug["id"], status="discarded")

        return StageResult(
            success=True,
            input_count=len(bug_data_list),
            output_count=len(deduplicated),
            cost_usd=result.cost_usd,
            metadata={"duplicates_removed": len(merged_ids)},
        )
