"""Validator stage — quick pass to ensure all bugs have been validated with working PoCs.

Assumes the Bug Hunter already attempted validation. For bugs marked as validated
with a working PoC, this is a pass-through. For bugs without PoCs or with failed
validation, the Validator attempts to write and execute a PoC itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

from bug_hunter.core.cli_wrapper import run_claude
from bug_hunter.core.database import list_bugs, update_bug
from bug_hunter.core.events import event_manager
from bug_hunter.utils.result_parser import parse_agent_result
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents")
SCHEMAS_DIR = Path(__file__).parent.parent.parent.parent / "schemas"


@register
class StrictValidatorStage(PipelineStage):

    @property
    def name(self) -> str:
        return "strict_validator"

    async def execute(self, context: StageContext) -> StageResult:
        bugs = list_bugs(context.engagement_id, status="found", run_id=context.run_id)
        if not bugs:
            self.write_output(context, "validated_bugs.json", [])
            self.write_output(context, "cannot_validate.json", [])
            return StageResult(success=True, input_count=0, output_count=0)

        eng_config = context.engagement["config"]
        eng_type = context.engagement["type"]
        infra_config = eng_config.get("engagement", {}).get("infra_config", "")

        # Point agent to scope file instead of inlining
        scope_file = self._stage_output_path(context, "scoper", "scope.json")

        stage_dir = self.get_stage_dir(context)
        pocs_dir = os.path.join(stage_dir, "pocs")
        os.makedirs(pocs_dir, exist_ok=True)

        total_cost = 0.0
        semaphore = asyncio.Semaphore(context.config.strict_validator.max_concurrent)

        async def validate_bug(bug: dict):
            nonlocal total_cost
            async with semaphore:
                bug_data = bug["bug_data"]
                bug_id = bug_data.get("id", "unknown")

                # Check if bug hunter already validated this bug
                poc = bug_data.get("poc")
                poc_result = poc.get("execution_result") if isinstance(poc, dict) else None
                already_validated = (
                    bug_data.get("validated") is True
                    and poc_result == "success"
                )

                if already_validated:
                    await event_manager.emit_log(
                        context.engagement_id, context.run_id, self.name,
                        f"Already validated: {bug_id} — passing through",
                    )
                    update_bug(bug["id"], status="validated", bug_data=bug_data)
                    return

                await event_manager.emit_log(
                    context.engagement_id, context.run_id, self.name,
                    f"Validating: {bug_id} — {bug_data.get('vuln_type', '')}",
                )

                record_dir, record_meta = self.prepare_agent_run(
                    context, "claude", f"validate_{bug_id}",
                    {"model": context.config.models.strict_validator, "bug_id": bug_id},
                )

                result = await self._validate_single_bug(
                    context, bug_data, infra_config, scope_file, eng_type,
                    pocs_dir, record_dir, record_meta,
                )
                total_cost += result.get("cost_usd", 0)

                if result.get("validated"):
                    bug_data.update(result.get("updates", {}))
                    bug_data["validated"] = True
                    update_bug(bug["id"], status="validated", bug_data=bug_data)
                else:
                    bug_data["cannot_validate_reason"] = result.get("reason", "Unknown")
                    update_bug(bug["id"], status="cannot_validate", bug_data=bug_data)

        await event_manager.emit_progress(
            context.engagement_id, context.run_id, self.name,
            0, len(bugs), f"Validating {len(bugs)} bugs",
        )

        tasks = [asyncio.create_task(validate_bug(bug)) for bug in bugs]
        completed = 0
        try:
            for coro in asyncio.as_completed(tasks):
                await coro
                completed += 1
                await event_manager.emit_progress(
                    context.engagement_id, context.run_id, self.name,
                    completed, len(bugs), f"Validated {completed}/{len(bugs)}",
                )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        validated = [b["bug_data"] for b in list_bugs(
            context.engagement_id, status="validated", run_id=context.run_id)]
        cannot_validate = [b["bug_data"] for b in list_bugs(
            context.engagement_id, status="cannot_validate", run_id=context.run_id)]
        self.write_output(context, "validated_bugs.json", validated)
        self.write_output(context, "cannot_validate.json", cannot_validate)

        return StageResult(
            success=True,
            input_count=len(bugs),
            output_count=len(validated),
            cost_usd=total_cost,
            metadata={"validated": len(validated), "cannot_validate": len(cannot_validate)},
        )

    async def _validate_single_bug(self, context, bug_data, infra_config, scope_file,
                                    eng_type, pocs_dir, record_dir, record_meta):
        bug_json = json.dumps(bug_data, indent=2)
        bug_id = bug_data.get("id", "unknown")
        destructive_policy = context.config.strict_validator.destructive_poc_policy

        if eng_type == "source_code":
            agent_file = os.path.join(AGENTS_DIR, "source_code", "validator.md")
        else:
            agent_file = os.path.join(AGENTS_DIR, "black_box", "validator.md")

        # Fallback to old agent files if new ones don't exist yet
        if not os.path.exists(agent_file):
            if eng_type == "source_code":
                agent_file = os.path.join(AGENTS_DIR, "source_code", "strict_validator.md")
            else:
                agent_file = os.path.join(AGENTS_DIR, "black_box", "strict_validator.md")

        prompt = f"""BUG:
{bug_json}

INFRASTRUCTURE ACCESS:
{infra_config}

APPLICATION CONTEXT: Read {scope_file}
DESTRUCTIVE POC POLICY: {destructive_policy}

Your output will be collected automatically via structured JSON output. Do not write results to any file."""

        result = await run_claude(
            prompt=prompt, agent_file=agent_file,
            model=context.config.models.strict_validator,
            cwd=pocs_dir,
            timeout=context.config.pipeline.subagent_timeout,
            record_dir=record_dir, record_metadata=record_meta,
            json_schema_file=str(SCHEMAS_DIR / "strict_validator.json"),
        )

        if not result.success:
            return {"validated": False, "reason": f"Validator failed: {result.error}",
                    "cost_usd": result.cost_usd}

        validation = parse_agent_result(result.result, ['validated', 'poc'], "strict_validator")
        validated = validation.get("validated", False)
        poc = validation.get("poc", {})
        # Don't accept validated=true without an actual PoC
        if validated and (not poc or not poc.get("code")):
            validated = False
            validation["reason"] = validation.get("reason", "") or "Marked validated but no PoC provided"
        return {
            "validated": validated,
            "reason": validation.get("reason", ""),
            "updates": {"poc": poc} if validated else {},
            "cost_usd": result.cost_usd,
        }
