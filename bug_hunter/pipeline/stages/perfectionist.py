"""Perfectionist stage — expand single-bug primitives to maximum impact via live PoC."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from bug_hunter.core.cli_wrapper import run_claude
from bug_hunter.core.database import list_bugs, update_bug
from bug_hunter.core.events import event_manager
from bug_hunter.utils.result_parser import parse_agent_result
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents")


@register
class PerfectionistStage(PipelineStage):

    @property
    def name(self) -> str:
        return "perfectionist"

    async def execute(self, context: StageContext) -> StageResult:
        bugs = list_bugs(context.engagement_id, status="validated", run_id=context.run_id)
        if not bugs:
            return StageResult(success=True, input_count=0, output_count=0)

        eng_config = context.engagement["config"]
        eng_type = context.engagement["type"]
        infra_config = eng_config.get("engagement", {}).get("infra_config", "")

        summaries = self.read_previous_output(context, "bug_hunter", "all_summaries.json")
        summaries_text = json.dumps(summaries, indent=2)[:30000] if summaries else "Not available"

        stage_dir = self.get_stage_dir(context)
        expanded_pocs_dir = os.path.join(stage_dir, "expanded_pocs")
        os.makedirs(expanded_pocs_dir, exist_ok=True)

        total_cost = 0.0
        semaphore = asyncio.Semaphore(context.config.perfectionist.max_concurrent)

        async def expand_bug(bug: dict):
            nonlocal total_cost
            async with semaphore:
                bug_data = bug["bug_data"]
                bug_id = bug_data.get("id", "unknown")

                await event_manager.emit_log(
                    context.engagement_id, context.run_id, self.name,
                    f"Expanding: {bug_id} - {bug_data.get('vuln_type', '')}",
                )

                record_dir, record_metadata = self.prepare_agent_run(
                    context,
                    "claude",
                    f"expand_{bug_id}",
                    {
                        "model": context.config.models.perfectionist,
                        "bug_id": bug_id,
                        "engagement_type": eng_type,
                    },
                )
                result = await self._expand_single_bug(
                    context, bug_data, infra_config, summaries_text,
                    eng_type, expanded_pocs_dir, record_dir, record_metadata,
                )
                total_cost += result.get("cost_usd", 0)

                if result.get("expanded"):
                    bug_data["expanded_primitives"] = result["expanded_primitives"]
                    update_bug(bug["id"], status="expanded", bug_data=bug_data)
                else:
                    update_bug(bug["id"], status="expanded", bug_data=bug_data)

                self.write_output(context, f"bug_{bug_id}_expanded.json", bug_data)

        await event_manager.emit_progress(
            context.engagement_id, context.run_id, self.name,
            0, len(bugs), f"Expanding {len(bugs)} bugs",
        )

        tasks = [expand_bug(bug) for bug in bugs]
        completed = 0
        for coro in asyncio.as_completed(tasks):
            await coro
            completed += 1
            await event_manager.emit_progress(
                context.engagement_id, context.run_id, self.name,
                completed, len(bugs), f"Expanded {completed}/{len(bugs)}",
            )

        return StageResult(
            success=True,
            input_count=len(bugs),
            output_count=len(bugs),
            cost_usd=total_cost,
        )

    async def _expand_single_bug(self, context: StageContext, bug_data: dict,
                                  infra_config: str, summaries_text: str,
                                  eng_type: str, expanded_pocs_dir: str,
                                  record_dir: str, record_metadata: dict) -> dict:
        """Expand a single bug's primitives."""
        bug_json = json.dumps(bug_data, indent=2)
        bug_id = bug_data.get("id", "unknown")

        if eng_type == "source_code":
            agent_file = os.path.join(AGENTS_DIR, "source_code", "perfectionist.md")
        else:
            agent_file = os.path.join(AGENTS_DIR, "black_box", "perfectionist.md")

        prompt = f"""You are expanding the primitives of a confirmed, validated security vulnerability
to its absolute maximum impact. Your job is to answer: "What is the maximum an attacker
can achieve with THIS ONE BUG ALONE?"

VALIDATED BUG WITH POC:
{bug_json}

INFRASTRUCTURE ACCESS:
{infra_config}

APPLICATION CONTEXT:
{summaries_text[:15000]}

INSTRUCTIONS:
1. This is SINGLE-BUG expansion only. Do NOT look at other bugs or suggest cross-bug chains.
2. Starting from the confirmed primitive, escalate step by step:
   - Can a read become a write? (e.g., SQLi SELECT → INSERT/UPDATE)
   - Can a write become code execution? (e.g., file write → webshell)
   - Can local access become remote access?
   - Can user-level access become admin/root?
3. For each expansion step, write and execute a PoC against the live infrastructure.
4. Save expanded PoCs to: {expanded_pocs_dir}/bug_{bug_id}_<expansion>.py
5. If an expansion cannot be demonstrated (environment limitation, would be destructive),
   document it as theoretical with the reason.

CRITICAL: Output ONLY a JSON object with this exact structure:
{{
  "expanded": true,
  "expanded_primitives": {{
    "demonstrated": [
      {{
        "primitive": "SQLi read -> SQLi write via UNION + INTO OUTFILE",
        "poc_file": "path/to/poc",
        "poc_code": "the code",
        "execution_result": "success"
      }}
    ],
    "theoretical": [
      {{
        "primitive": "SQLi write -> RCE via webshell",
        "reason_not_demonstrated": "Web root not writable in test environment"
      }}
    ]
  }}
}}"""

        result = await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.perfectionist,
            cwd=expanded_pocs_dir,
            timeout=context.config.pipeline.subagent_timeout,
            record_dir=record_dir,
            record_metadata=record_metadata,
        )

        if not result.success:
            return {"expanded": False, "cost_usd": result.cost_usd}

        expansion = parse_agent_result(result.result, ['expanded', 'expanded_primitives'], "perfectionist")
        return {
            "expanded": expansion.get("expanded", False),
            "expanded_primitives": expansion.get("expanded_primitives", {"demonstrated": [], "theoretical": []}),
            "cost_usd": result.cost_usd,
        }
