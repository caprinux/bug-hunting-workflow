"""Scope Validator stage — fast pass to remove bugs strictly outside scope.

This is a quick filter, not a deep analysis. It reads the scope definition
and program rules, then removes only bugs that CLEARLY violate them.
Ambiguous cases pass through — the human decides later.
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

        # Load scope notes from Scoper if available
        scope_data = self.read_previous_output(context, "scoper", "scope.json")
        scope_notes = {}
        if scope_data:
            scope_notes = scope_data.get("scope_notes", {})

        bug_data_list = [b["bug_data"] for b in bugs]

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Quick scope check on {len(bug_data_list)} findings",
        )

        qualifying = json.dumps(scope_notes.get("qualifying", []))
        non_qualifying = json.dumps(scope_notes.get("non_qualifying", []))
        excluded = json.dumps(scope_notes.get("excluded_paths", []))

        prompt = f"""You are performing a FAST scope validation pass on security findings.

SCOPE DEFINITION:
{scope_def}

QUALIFYING VULNERABILITIES: {qualifying}
NON-QUALIFYING VULNERABILITIES: {non_qualifying}
EXCLUDED PATHS/COMPONENTS: {excluded}

FINDINGS ({len(bug_data_list)} total):
{json.dumps(bug_data_list, indent=2)[:80000]}

RULES:
- REMOVE only findings that STRICTLY do not follow the scope and rules
- KEEP any finding that is ambiguous — when in doubt, keep it
- This is a fast pass, not a deep analysis
- Don't evaluate severity or exploitability — just scope compliance

For each finding, output its id and whether it's "in_scope" or "out_of_scope".
For out_of_scope findings, briefly note which rule excludes it.

Output JSON:
{{
  "in_scope": ["bug-id-1", "bug-id-2"],
  "out_of_scope": [
    {{"id": "bug-id-3", "reason": "Non-qualifying: informational version disclosure"}}
  ]
}}"""

        agent_file = os.path.join(AGENTS_DIR, "shared", "scope_validator.md")
        if not os.path.exists(agent_file):
            agent_file = None

        record_dir, record_meta = self.prepare_agent_run(
            context, "claude", "scope_validation",
            {"finding_count": len(bug_data_list)},
        )

        result = await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.strict_validator,  # reuse validator model
            timeout=min(context.config.pipeline.subagent_timeout, 300),  # fast pass
            record_dir=record_dir,
            record_metadata=record_meta,
        )

        if not result.success:
            # On failure, pass everything through (don't block pipeline)
            self.write_output(context, "in_scope.json", bug_data_list)
            self.write_output(context, "out_of_scope.json", [])
            return StageResult(
                success=True,
                input_count=len(bugs),
                output_count=len(bugs),
                cost_usd=result.cost_usd,
                metadata={"scope_check_failed": True},
            )

        scope_result = result.result if isinstance(result.result, dict) else {}
        in_scope_ids = set(scope_result.get("in_scope", []))
        out_of_scope_list = scope_result.get("out_of_scope", [])
        out_of_scope_map = {}
        for item in out_of_scope_list:
            if isinstance(item, dict):
                out_of_scope_map[item.get("id", "")] = item.get("reason", "")
            elif isinstance(item, str):
                out_of_scope_map[item] = ""

        in_scope_bugs = []
        out_of_scope_bugs = []

        for bug in bugs:
            bid = bug["bug_data"].get("id")
            if bid in out_of_scope_map:
                merged = dict(bug["bug_data"])
                merged["scope_reasoning"] = out_of_scope_map[bid]
                update_bug(bug["id"], status="out_of_scope", bug_data=merged)
                out_of_scope_bugs.append(merged)
            else:
                # Keep it — either explicitly in_scope or not mentioned (ambiguous = keep)
                in_scope_bugs.append(bug["bug_data"])

        self.write_output(context, "in_scope.json", in_scope_bugs)
        self.write_output(context, "out_of_scope.json", out_of_scope_bugs)

        if out_of_scope_bugs:
            await event_manager.emit_log(
                context.engagement_id, context.run_id, self.name,
                f"Removed {len(out_of_scope_bugs)} out-of-scope findings, kept {len(in_scope_bugs)}",
            )

        return StageResult(
            success=True,
            input_count=len(bugs),
            output_count=len(in_scope_bugs),
            cost_usd=result.cost_usd,
            metadata={"in_scope": len(in_scope_bugs), "out_of_scope": len(out_of_scope_bugs)},
        )
