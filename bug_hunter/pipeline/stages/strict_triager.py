"""Triager stage — bug bounty triager that judges scope, impact, and validity.

Acts as a strict bug bounty triager:
- Valid: real security impact, in scope, properly validated
- Informational: true findings with no direct security impact (useful intelligence)
- Out of Scope: real bugs but outside the engagement's scope definition
- Discarded: false positives, contrived scenarios, no real impact
"""

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
            self.write_output(context, "out_of_scope.json", [])
            self.write_output(context, "discarded.json", [])
            return StageResult(success=True, input_count=0, output_count=0)

        eng_config = context.engagement["config"]
        scope_def = eng_config.get("engagement", {}).get("scope_definition", "")

        # Load scope data for qualifying/non-qualifying vuln types
        scope_data = self.read_previous_output(context, "scoper", "scope.json")
        scope_notes = {}
        if scope_data:
            scope_notes = scope_data.get("scope_notes", {})

        bug_data_list = [b["bug_data"] for b in bugs]
        contrived_threshold = context.config.strict_triager.contrived_threshold

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Triaging {len(bug_data_list)} findings as bug bounty triager",
        )

        prompt = f"""You are a bug bounty triager evaluating security vulnerability submissions.
Your job is to strictly judge each bug and categorize it.

SCOPE DEFINITION:
{scope_def}

QUALIFYING VULNERABILITIES: {json.dumps(scope_notes.get("qualifying", []))}
NON-QUALIFYING VULNERABILITIES: {json.dumps(scope_notes.get("non_qualifying", []))}
EXCLUDED PATHS: {json.dumps(scope_notes.get("excluded_paths", []))}

FINDINGS ({len(bug_data_list)} total):
{json.dumps(bug_data_list, indent=2)[:80000]}

CONTRIVED THRESHOLD: {contrived_threshold}
(If exploitation requires {contrived_threshold}+ improbable preconditions an attacker cannot control, it's contrived)

CATEGORIZE each finding into one of four buckets:

1. **VALID** — Real security impact, in scope, properly validated
   - Assign severity: critical / high / medium / low
   - IDOR exposing sensitive data = valid (not informational)
   - Any bug with working PoC demonstrating real impact = valid

2. **INFORMATIONAL** — True findings, no direct exploitable security impact
   - Internal IPs, version strings, stack traces, architecture details
   - Useful as intelligence but not a security vulnerability

3. **OUT OF SCOPE** — Real bugs but outside the scope definition
   - Vulnerability in excluded component
   - Non-qualifying vulnerability type
   - Explain what scope rule excludes it

4. **DISCARDED** — Not real or not useful
   - False positives
   - Contrived exploitation scenarios ({contrived_threshold}+ improbable preconditions)
   - Self-XSS, clickjacking on non-sensitive pages, missing headers with no impact

For each finding, provide triager_notes explaining your judgment.

Output JSON:
{{
  "valid": [{{...bugs with "severity" and "triager_notes" added...}}],
  "informational": [{{...}}],
  "out_of_scope": [{{...with "scope_reasoning"...}}],
  "discarded": [{{...with "triager_notes"...}}]
}}"""

        agent_file = os.path.join(AGENTS_DIR, "shared", "triager.md")
        if not os.path.exists(agent_file):
            agent_file = os.path.join(AGENTS_DIR, "shared", "strict_triager.md")

        record_dir, record_meta = self.prepare_agent_run(
            context, "claude", "triage",
            {"model": context.config.models.strict_triager, "finding_count": len(bug_data_list)},
        )

        result = await run_claude(
            prompt=prompt, agent_file=agent_file,
            model=context.config.models.strict_triager,
            timeout=context.config.pipeline.subagent_timeout,
            record_dir=record_dir, record_metadata=record_meta,
        )

        if not result.success:
            for bug in bugs:
                update_bug(bug["id"], status="triage_failed")
            self.write_output(context, "confirmed_bugs.json", [])
            self.write_output(context, "informational.json", [])
            self.write_output(context, "out_of_scope.json", [])
            self.write_output(context, "discarded.json", [])
            self.write_output(context, "triage_failed.json", bug_data_list)

            await event_manager.emit_error(
                context.engagement_id, context.run_id, self.name,
                f"Triager failed — {len(bugs)} bugs moved to triage_failed for human review",
            )
            return StageResult(
                success=True, input_count=len(bugs), output_count=0,
                cost_usd=result.cost_usd,
                metadata={"triage_failed": True, "triage_failed_count": len(bugs)},
            )

        triage_result = result.result if isinstance(result.result, dict) else {}
        valid = triage_result.get("valid", triage_result.get("confirmed", []))
        informational = triage_result.get("informational", [])
        out_of_scope = triage_result.get("out_of_scope", [])
        discarded = triage_result.get("discarded", [])

        self.write_output(context, "confirmed_bugs.json", valid)
        self.write_output(context, "informational.json", informational)
        self.write_output(context, "out_of_scope.json", out_of_scope)
        self.write_output(context, "discarded.json", discarded)

        valid_ids = {f.get("id") for f in valid}
        info_ids = {f.get("id") for f in informational}
        oos_ids = {f.get("id") for f in out_of_scope}
        discarded_ids = {f.get("id") for f in discarded}

        for bug in bugs:
            bid = bug["bug_data"].get("id")
            if bid in valid_ids:
                matched = next((c for c in valid if c.get("id") == bid), None)
                if matched:
                    update_bug(bug["id"], status="confirmed", bug_data=matched)
            elif bid in info_ids:
                matched = next((i for i in informational if i.get("id") == bid), None)
                if matched:
                    update_bug(bug["id"], status="informational", bug_data=matched)
            elif bid in oos_ids:
                update_bug(bug["id"], status="out_of_scope")
            elif bid in discarded_ids:
                update_bug(bug["id"], status="discarded")

        return StageResult(
            success=True,
            input_count=len(bugs),
            output_count=len(valid),
            cost_usd=result.cost_usd,
            metadata={
                "valid": len(valid),
                "informational": len(informational),
                "out_of_scope": len(out_of_scope),
                "discarded": len(discarded),
            },
        )
