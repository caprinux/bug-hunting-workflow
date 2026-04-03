"""Testing Setup stage — sets up Docker/testing environments for bug revalidation."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from bug_hunter.core.cli_wrapper import run_agent
from bug_hunter.core.events import event_manager
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = Path(__file__).parent.parent.parent.parent / "agents"


@register
class TestingSetupStage(PipelineStage):

    @property
    def name(self) -> str:
        return "testing_setup"

    async def execute(self, context: StageContext) -> StageResult:
        if context.run_type != "revalidation":
            return StageResult(success=True, metadata={"skipped": "not_revalidation"})

        instructions = context.rehunt_target  # reused field for setup instructions
        if not instructions:
            return StageResult(success=False, error="No setup instructions provided")

        eng_config = context.engagement["config"]
        eng_type = context.engagement["type"]

        # Get source path from latest setup.json
        setup_data = self.read_previous_output(context, "setup", "setup.json")
        source_path = ""
        if setup_data and "source" in setup_data:
            source_path = setup_data["source"]["local_path"]
        if not source_path:
            source_path = eng_config.get("engagement", {}).get("source_path", "")

        stage_dir = self.get_stage_dir(context)

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            "Setting up testing environment...",
        )

        agent_file = str(AGENTS_DIR / "shared" / "testing_setup.md")

        # Build prompt with setup instructions and source context
        prompt_parts = [
            f"SETUP INSTRUCTIONS:\n{instructions}",
        ]
        if source_path and os.path.isdir(source_path):
            prompt_parts.append(f"\nSOURCE CODE ROOT: {source_path}")

        scope_file = self._stage_output_path(context, "scoper", "scope.json")
        if os.path.exists(scope_file):
            prompt_parts.append(f"\nAPPLICATION CONTEXT: Read {scope_file}")

        prompt_parts.append(
            "\nAfter setting up the environment, output a JSON object with:"
            "\n- `status`: 'ready' or 'failed'"
            "\n- `base_url`: the URL where the application is accessible (e.g. http://localhost:8080)"
            "\n- `services`: list of running services with name, port, and health status"
            "\n- `notes`: any important notes for the validator (credentials, endpoints, etc.)"
        )

        prompt = "\n".join(prompt_parts)

        record_dir, record_meta = self.prepare_agent_run(
            context, self._agent_name_for_model(context.config.models.strict_validator), "testing_setup",
            {"model": context.config.models.strict_validator, "engagement_type": eng_type},
        )

        result = await run_agent(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.strict_validator,
            cwd=source_path or None,
            timeout=context.config.pipeline.subagent_timeout,
            record_dir=record_dir,
            record_metadata=record_meta,
        )

        if not result.success:
            return StageResult(success=False, error=result.error, cost_usd=result.cost_usd)

        # Parse setup result
        from bug_hunter.utils.result_parser import parse_agent_result
        setup_result = parse_agent_result(result.result, ["status", "base_url"], "testing_setup")

        if not setup_result:
            return StageResult(success=False, error="Setup agent returned no structured output", cost_usd=result.cost_usd)

        if setup_result.get("status") != "ready" or not setup_result.get("base_url"):
            self.write_output(context, "testing_setup.json", setup_result)
            reason = setup_result.get("notes", setup_result.get("status", "unknown"))
            if not setup_result.get("base_url") and setup_result.get("status") == "ready":
                reason = "Setup reported ready but no base_url provided"
            return StageResult(
                success=False,
                error=f"Testing environment setup failed: {reason}",
                cost_usd=result.cost_usd,
            )

        self.write_output(context, "testing_setup.json", setup_result)

        # Write infra info as plain text for the validator to read
        if setup_result.get("base_url"):
            infra_notes = f"Testing environment: {setup_result['base_url']}"
            if setup_result.get("notes"):
                infra_notes += f"\n{setup_result['notes']}"
            stage_dir = self.get_stage_dir(context)
            with open(os.path.join(stage_dir, "testing_infra.txt"), "w") as f:
                f.write(infra_notes)

        return StageResult(
            success=True,
            cost_usd=result.cost_usd,
            metadata=setup_result,
        )
