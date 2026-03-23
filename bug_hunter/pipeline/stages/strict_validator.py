"""Strict Validator stage — prove exploitability via PoC execution against live infra."""

from __future__ import annotations

import asyncio
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
class StrictValidatorStage(PipelineStage):

    @property
    def name(self) -> str:
        return "strict_validator"

    async def execute(self, context: StageContext) -> StageResult:
        bugs = list_bugs(context.engagement_id, status="in_scope", run_id=context.run_id)
        if not bugs:
            self.write_output(context, "validated_bugs.json", [])
            self.write_output(context, "cannot_validate.json", [])
            return StageResult(success=True, input_count=0, output_count=0)

        eng_config = context.engagement["config"]
        eng_type = context.engagement["type"]
        infra_config = eng_config.get("engagement", {}).get("infra_config", "")

        summaries = self.read_previous_output(context, "bug_hunter", "all_summaries.json")
        summaries_text = json.dumps(summaries, indent=2)[:30000] if summaries else "Not available"

        stage_dir = self.get_stage_dir(context)
        pocs_dir = os.path.join(stage_dir, "pocs")
        os.makedirs(pocs_dir, exist_ok=True)

        validated = []
        cannot_validate = []
        total_cost = 0.0

        semaphore = asyncio.Semaphore(context.config.strict_validator.max_concurrent)

        async def validate_bug(bug: dict):
            nonlocal total_cost
            async with semaphore:
                bug_data = bug["bug_data"]
                await event_manager.emit_log(
                    context.engagement_id, context.run_id, self.name,
                    f"Validating: {bug_data.get('id', 'unknown')} - {bug_data.get('vuln_type', '')}",
                )

                result = await self._validate_single_bug(
                    context, bug_data, infra_config, summaries_text, eng_type, pocs_dir,
                )
                total_cost += result.get("cost_usd", 0)

                if result.get("validated"):
                    bug_data.update(result.get("updates", {}))
                    validated.append(bug_data)
                    update_bug(bug["id"], status="validated", bug_data=bug_data)
                else:
                    bug_data["cannot_validate_reason"] = result.get("reason", "Unknown")
                    cannot_validate.append(bug_data)
                    update_bug(bug["id"], status="cannot_validate", bug_data=bug_data)

        await event_manager.emit_progress(
            context.engagement_id, context.run_id, self.name,
            0, len(bugs), f"Validating {len(bugs)} bugs",
        )

        tasks = [validate_bug(bug) for bug in bugs]
        completed = 0
        for coro in asyncio.as_completed(tasks):
            await coro
            completed += 1
            await event_manager.emit_progress(
                context.engagement_id, context.run_id, self.name,
                completed, len(bugs), f"Validated {completed}/{len(bugs)}",
            )

        self.write_output(context, "validated_bugs.json", validated)
        self.write_output(context, "cannot_validate.json", cannot_validate)

        return StageResult(
            success=True,
            input_count=len(bugs),
            output_count=len(validated),
            cost_usd=total_cost,
            metadata={"validated": len(validated), "cannot_validate": len(cannot_validate)},
        )

    async def _validate_single_bug(self, context: StageContext, bug_data: dict,
                                    infra_config: str, summaries_text: str,
                                    eng_type: str, pocs_dir: str) -> dict:
        """Validate a single bug with PoC execution."""
        bug_json = json.dumps(bug_data, indent=2)
        bug_id = bug_data.get("id", "unknown")
        destructive_policy = context.config.strict_validator.destructive_poc_policy
        poc_language = context.config.strict_validator.poc_language

        if eng_type == "source_code":
            agent_file = os.path.join(AGENTS_DIR, "source_code", "strict_validator.md")
            method_instructions = """METHOD:
1. Trace the data flow statically through the source code
2. Understand how user input reaches the vulnerable sink
3. Identify sanitization/validation in the path
4. Write a PoC that demonstrates exploitability
5. Execute the PoC against the live infrastructure
6. Report the result"""
        else:
            agent_file = os.path.join(AGENTS_DIR, "black_box", "strict_validator.md")
            method_instructions = """METHOD:
1. Analyze the Bug Hunter's HTTP evidence (request/response)
2. Reproduce the triggering request
3. Verify the response confirms exploitation
4. Write a clean, standalone PoC
5. Execute the PoC
6. Report the result"""

        prompt = f"""Validate the following security vulnerability by writing and executing a proof-of-concept.

BUG FINDING:
{bug_json}

INFRASTRUCTURE ACCESS:
{infra_config}

APPLICATION CONTEXT:
{summaries_text[:15000]}

{method_instructions}

DESTRUCTIVE POC POLICY: {destructive_policy}
- If the PoC would be destructive (DoS, data deletion, resource exhaustion):
  {"Route to cannot-validate with note: 'likely exploitable, PoC destructive'" if destructive_policy == "cannot_validate" else "Proceed with execution"}

DEFAULT POC LANGUAGE: {poc_language} (you may use another language if more appropriate)

Write the PoC to: {pocs_dir}/bug_{bug_id}_poc.py (or appropriate extension)

Output a JSON object:
{{
  "validated": true/false,
  "poc": {{
    "language": "{poc_language}",
    "code": "the PoC source code",
    "file": "path to saved PoC file",
    "execution_result": "success|failure|error|destructive_skipped",
    "output": "execution output"
  }},
  "reason": "if not validated, explain why",
  "is_destructive": true/false
}}"""

        result = await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.strict_validator,
            timeout=context.config.pipeline.subagent_timeout,
        )

        if not result.success:
            return {"validated": False, "reason": f"Validator failed: {result.error}",
                    "cost_usd": result.cost_usd}

        validation = result.result or {}
        is_validated = validation.get("validated", False)
        poc_data = validation.get("poc", {})

        return {
            "validated": is_validated,
            "reason": validation.get("reason", ""),
            "updates": {"poc": poc_data} if is_validated else {},
            "cost_usd": result.cost_usd,
        }
