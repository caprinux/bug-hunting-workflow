"""Strict Triager stage — evaluate impact, filter noise, produce three output categories."""

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
class StrictTriagerStage(PipelineStage):

    @property
    def name(self) -> str:
        return "strict_triager"

    async def execute(self, context: StageContext) -> StageResult:
        bugs = list_bugs(context.engagement_id, status="expanded", run_id=context.run_id)
        if not bugs:
            bugs = list_bugs(context.engagement_id, status="validated", run_id=context.run_id)

        if not bugs:
            self.write_output(context, "confirmed_bugs.json", [])
            self.write_output(context, "informational.json", [])
            self.write_output(context, "discarded.json", [])
            return StageResult(success=True, input_count=0, output_count=0)

        bug_data_list = [b["bug_data"] for b in bugs]
        contrived_threshold = context.config.strict_triager.contrived_threshold
        severity_floor = context.config.strict_triager.severity_floor

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Triaging {len(bug_data_list)} findings",
        )

        prompt = f"""You are the final quality gate for security vulnerability findings. Your job is to
aggressively question each bug and categorize it into one of three buckets.

FINDINGS ({len(bug_data_list)} total):
{json.dumps(bug_data_list, indent=2)[:80000]}

CONFIGURATION:
- Contrived threshold: {contrived_threshold} (if exploitation requires {contrived_threshold}+
  improbable preconditions that an attacker cannot control, it's contrived)
- Severity floor: {severity_floor} (minimum severity to survive as a confirmed bug)

THREE OUTPUT CATEGORIES:

1. CONFIRMED BUGS — Real vulnerabilities with demonstrated security impact.
   - IDOR exposing sensitive data (PII, private messages, credentials) = confirmed bug
   - Any bug with a working PoC demonstrating real security impact = confirmed
   - Evaluate both the base bug AND the Perfectionist's demonstrated expansions

2. INFORMATIONAL — True and factual findings with no direct security impact, but valuable as intelligence.
   - Internal IP addresses leaked via error messages or headers
   - Software version strings exposed
   - Stack traces in error responses
   - Architecture details, debug information
   - These are NOT bugs but are useful for infrastructure mapping

3. DISCARDED — Kill these:
   - Findings requiring {contrived_threshold}+ improbable preconditions an attacker cannot control
   - Findings that are not real vulnerabilities
   - Findings below the severity floor of "{severity_floor}"
   - Findings that are purely theoretical with no demonstrated impact

For each finding, assign a severity (critical/high/medium/low/informational) and write
detailed triager notes explaining your reasoning.

Output a JSON object:
{{
  "confirmed": [
    // bugs with added "severity" and "triager_notes" fields
  ],
  "informational": [
    // informational findings with "severity": "informational" and "triager_notes"
  ],
  "discarded": [
    // discarded findings with "triager_notes" explaining why
  ]
}}"""

        agent_file = os.path.join(AGENTS_DIR, "shared", "strict_triager.md")

        result = await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.strict_triager,
            timeout=context.config.pipeline.subagent_timeout,
        )

        if not result.success:
            for bug in bugs:
                update_bug(bug["id"], status="confirmed")
            self.write_output(context, "confirmed_bugs.json", bug_data_list)
            self.write_output(context, "informational.json", [])
            self.write_output(context, "discarded.json", [])
            return StageResult(
                success=True, input_count=len(bugs), output_count=len(bugs),
                cost_usd=result.cost_usd, metadata={"triage_failed": True},
            )

        triage_result = result.result or {}
        confirmed = triage_result.get("confirmed", [])
        informational = triage_result.get("informational", [])
        discarded = triage_result.get("discarded", [])

        self.write_output(context, "confirmed_bugs.json", confirmed)
        self.write_output(context, "informational.json", informational)
        self.write_output(context, "discarded.json", discarded)

        confirmed_ids = {f.get("id") for f in confirmed}
        informational_ids = {f.get("id") for f in informational}
        discarded_ids = {f.get("id") for f in discarded}

        for bug in bugs:
            bid = bug["bug_data"].get("id")
            if bid in confirmed_ids:
                matched = next((c for c in confirmed if c.get("id") == bid), None)
                if matched:
                    update_bug(bug["id"], status="confirmed", bug_data=matched)
            elif bid in informational_ids:
                matched = next((i for i in informational if i.get("id") == bid), None)
                if matched:
                    update_bug(bug["id"], status="informational", bug_data=matched)
            elif bid in discarded_ids:
                update_bug(bug["id"], status="discarded")

        return StageResult(
            success=True,
            input_count=len(bugs),
            output_count=len(confirmed),
            cost_usd=result.cost_usd,
            metadata={
                "confirmed": len(confirmed),
                "informational": len(informational),
                "discarded": len(discarded),
            },
        )
