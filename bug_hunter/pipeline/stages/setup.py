"""Setup stage — tool checks, source acquisition, environment preparation."""

from __future__ import annotations

import json
import logging

from bug_hunter.core.events import event_manager
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register
from bug_hunter.utils.source_acquisition import acquire_source
from bug_hunter.utils.tools import check_and_install_tools, tools_report

logger = logging.getLogger(__name__)


@register
class SetupStage(PipelineStage):

    @property
    def name(self) -> str:
        return "setup"

    async def execute(self, context: StageContext) -> StageResult:
        stage_dir = self.get_stage_dir(context)
        eng_config = context.engagement["config"]
        eng_type = context.engagement["type"]

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            "Checking tool dependencies...",
        )

        tool_results = await check_and_install_tools(
            eng_type, auto_install=context.config.pipeline.auto_install_tools,
        )
        report = tools_report(tool_results)
        self.write_output(context, "setup.json", report)

        missing_required = [
            t for t in tool_results
            if not t.available and t.name in ("claude", "git", "python3")
        ]
        if missing_required:
            names = ", ".join(t.name for t in missing_required)
            return StageResult(
                success=False,
                error=f"Required tools missing and could not be installed: {names}",
                metadata=report,
            )

        if eng_type == "source_code":
            await event_manager.emit_log(
                context.engagement_id, context.run_id, self.name,
                "Acquiring source code...",
            )

            source_result = await acquire_source(
                source_path=eng_config.get("engagement", {}).get("source_path", ""),
                source_repo=eng_config.get("engagement", {}).get("source_repo", ""),
                output_dir=context.config.pipeline.output_dir,
                run_id=context.run_id,
            )

            if not source_result.success:
                return StageResult(
                    success=False,
                    error=f"Source acquisition failed: {source_result.error}",
                )

            report["source"] = {
                "local_path": source_result.local_path,
                "repo_url": source_result.repo_url,
                "branch": source_result.branch,
                "commit": source_result.commit,
            }
            self.write_output(context, "setup.json", report)

            await event_manager.emit_log(
                context.engagement_id, context.run_id, self.name,
                f"Source code ready at: {source_result.local_path}",
            )

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            "Setup complete",
        )

        return StageResult(success=True, metadata=report)
