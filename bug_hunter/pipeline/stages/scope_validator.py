"""Scope Validator stage — filter findings against engagement scope definition."""

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
class ScopeValidatorStage(PipelineStage):

    @property
    def name(self) -> str:
        return "scope_validator"

    async def execute(self, context: StageContext) -> StageResult:
        bugs = list_bugs(context.engagement_id, status="found", run_id=context.run_id)
        if not bugs:
            self.write_output(context, "in_scope.json", [])
            self.write_output(context, "out_of_scope.json", [])
            return StageResult(success=True, input_count=0, output_count=0)

        eng_config = context.engagement["config"]
        scope_def = eng_config.get("engagement", {}).get("scope_definition", "")

        bug_data_list = [b["bug_data"] for b in bugs]

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Validating scope for {len(bug_data_list)} findings",
        )

        prompt = f"""You are checking whether security findings are within the defined scope of this engagement.

SCOPE DEFINITION:
{scope_def}

FINDINGS ({len(bug_data_list)} total):
{json.dumps(bug_data_list, indent=2)[:80000]}

For each finding, determine if it is IN SCOPE or OUT OF SCOPE based on the scope definition.
Consider: target components, vulnerability classes, authentication context, and any specific exclusions.

Output a JSON object:
{{
  "in_scope": [
    // findings that are within scope, with an added "scope_reasoning" field
  ],
  "out_of_scope": [
    // findings that are outside scope, with "scope_reasoning" explaining why
  ]
}}"""

        agent_file = os.path.join(AGENTS_DIR, "shared", "scope_validator.md")

        result = await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.scope_validator,
            timeout=context.config.pipeline.subagent_timeout,
        )

        if not result.success:
            self.write_output(context, "in_scope.json", bug_data_list)
            self.write_output(context, "out_of_scope.json", [])
            return StageResult(
                success=True,
                input_count=len(bug_data_list),
                output_count=len(bug_data_list),
                cost_usd=result.cost_usd,
                metadata={"scope_check_failed": True},
            )

        scope_result = result.result or {}
        in_scope = scope_result.get("in_scope", bug_data_list)
        out_of_scope = scope_result.get("out_of_scope", [])

        self.write_output(context, "in_scope.json", in_scope)
        self.write_output(context, "out_of_scope.json", out_of_scope)

        out_of_scope_ids = {f.get("id") for f in out_of_scope}
        in_scope_ids = {f.get("id") for f in in_scope}

        for bug in bugs:
            bid = bug["bug_data"].get("id")
            if bid in out_of_scope_ids:
                update_bug(bug["id"], status="out_of_scope")
            elif bid in in_scope_ids:
                update_bug(bug["id"], status="in_scope")

        return StageResult(
            success=True,
            input_count=len(bug_data_list),
            output_count=len(in_scope),
            cost_usd=result.cost_usd,
            metadata={"in_scope": len(in_scope), "out_of_scope": len(out_of_scope)},
        )
