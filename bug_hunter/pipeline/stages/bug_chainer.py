"""Bug Chainer stage — cross-bug analysis, chain construction, re-hunt suggestions."""

from __future__ import annotations

import json
import logging
import os

from bug_hunter.core.cli_wrapper import run_claude
from bug_hunter.core.database import create_chain, list_bugs
from bug_hunter.core.events import event_manager
from bug_hunter.utils.result_parser import parse_agent_result
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents")


@register
class BugChainerStage(PipelineStage):

    @property
    def name(self) -> str:
        return "bug_chainer"

    async def execute(self, context: StageContext) -> StageResult:
        confirmed_bugs = list_bugs(context.engagement_id, status="confirmed")
        informational = list_bugs(context.engagement_id, status="informational")

        if not confirmed_bugs:
            self.write_output(context, "individual_bugs.json", [])
            self.write_output(context, "demonstrated_chains.json", [])
            self.write_output(context, "proposed_chains.json", [])
            self.write_output(context, "rehunt_suggestions.json", [])
            return StageResult(success=True, input_count=0, output_count=0)

        eng_config = context.engagement["config"]
        infra_config = eng_config.get("engagement", {}).get("infra_config", "")

        summaries_file = self._stage_output_path(context, "bug_hunter", "all_summaries.json")

        confirmed_data = [b["bug_data"] for b in confirmed_bugs]
        intel_data = [b["bug_data"] for b in informational]

        stage_dir = self.get_stage_dir(context)
        chain_pocs_dir = os.path.join(stage_dir, "chain_pocs")
        os.makedirs(chain_pocs_dir, exist_ok=True)

        # Write data to files so LLM reads on its own
        confirmed_file = os.path.join(stage_dir, "input_confirmed.json")
        with open(confirmed_file, "w") as f:
            json.dump(confirmed_data, f, indent=2)
        intel_file = os.path.join(stage_dir, "input_intel.json")
        with open(intel_file, "w") as f:
            json.dump(intel_data, f, indent=2)

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Chaining {len(confirmed_data)} confirmed bugs with {len(intel_data)} intel findings",
        )

        prompt = f"""You are performing cross-bug analysis to chain confirmed vulnerabilities together
for maximum combined security impact.

CONFIRMED BUGS ({len(confirmed_data)} total): Read {os.path.abspath(confirmed_file)}

INTELLIGENCE FILE ({len(intel_data)} informational findings): Read {os.path.abspath(intel_file)}

APPLICATION CONTEXT: Read {summaries_file}

INFRASTRUCTURE ACCESS:
{infra_config}

INSTRUCTIONS:
1. Analyze all confirmed bugs and their expanded primitives
2. Identify bugs whose primitives can be chained together for higher combined impact
3. Consider the intelligence file for context (internal IPs for SSRF targets,
   version strings for specific exploit paths, etc.)
4. For each chain:
   - Reason about execution order and state dependencies
   - Determine if step N sets up preconditions for step N+1
   - Where possible, write and execute a combined PoC to demonstrate the chain
   - Save chain PoCs to: {chain_pocs_dir}/
5. Suggest re-hunt targets: specific bug classes that would enable higher-impact chains
   (these will require human approval before re-hunting)

CRITICAL: Output ONLY a JSON object with this exact structure:
{{
  "individual_bugs": [
    // all confirmed bugs (unchanged, for the report)
  ],
  "demonstrated_chains": [
    {{
      "id": "chain-001",
      "bug_ids": ["bug-001", "bug-003"],
      "description": "SSRF + leaked internal IP -> internal admin panel access",
      "combined_impact": "Full admin access to internal services",
      "execution_order": "Step 1: Use SSRF (bug-001) to reach internal IP from intel...",
      "status": "demonstrated",
      "combined_poc_file": "chain_pocs/chain_001.py",
      "severity": "critical"
    }}
  ],
  "proposed_chains": [
    {{
      "id": "chain-002",
      "bug_ids": ["bug-002", "bug-005"],
      "description": "XSS + CSRF -> account takeover",
      "combined_impact": "...",
      "execution_order": "...",
      "status": "proposed",
      "severity": "high"
    }}
  ],
  "rehunt_suggestions": [
    {{
      "target_bug_class": "Stored XSS in admin panel",
      "reason": "Would chain with confirmed CSRF (bug-004) for persistent admin account takeover",
      "priority": "high"
    }}
  ]
}}"""

        agent_file = os.path.join(AGENTS_DIR, "shared", "bug_chainer.md")
        record_dir, record_metadata = self.prepare_agent_run(
            context,
            "claude",
            "bug_chainer",
            {
                "model": context.config.models.bug_chainer,
                "confirmed_count": len(confirmed_data),
                "informational_count": len(intel_data),
            },
        )

        result = await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.bug_chainer,
            timeout=context.config.pipeline.subagent_timeout * 2,
            record_dir=record_dir,
            record_metadata=record_metadata,
        )

        if not result.success:
            self.write_output(context, "individual_bugs.json", confirmed_data)
            self.write_output(context, "demonstrated_chains.json", [])
            self.write_output(context, "proposed_chains.json", [])
            self.write_output(context, "rehunt_suggestions.json", [])
            return StageResult(
                success=True, input_count=len(confirmed_data), output_count=len(confirmed_data),
                cost_usd=result.cost_usd, metadata={"chaining_failed": True},
            )

        chainer_result = parse_agent_result(result.result, ['demonstrated_chains', 'proposed_chains', 'individual_bugs'], "bug_chainer")
        individual = chainer_result.get("individual_bugs", confirmed_data)
        demonstrated = chainer_result.get("demonstrated_chains", [])
        proposed = chainer_result.get("proposed_chains", [])
        rehunt = chainer_result.get("rehunt_suggestions", [])

        self.write_output(context, "individual_bugs.json", individual)
        self.write_output(context, "demonstrated_chains.json", demonstrated)
        self.write_output(context, "proposed_chains.json", proposed)
        self.write_output(context, "rehunt_suggestions.json", rehunt)

        for chain_data in demonstrated + proposed:
            create_chain(context.engagement_id, chain_data, run_id=context.run_id)

        return StageResult(
            success=True,
            input_count=len(confirmed_data),
            output_count=len(individual),
            cost_usd=result.cost_usd,
            metadata={
                "demonstrated_chains": len(demonstrated),
                "proposed_chains": len(proposed),
                "rehunt_suggestions": len(rehunt),
            },
        )
