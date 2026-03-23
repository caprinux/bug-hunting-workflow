"""Pipeline orchestrator — manages stage sequencing, retries, state, and concurrency."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from bug_hunter.core.config import AppConfig, config_to_dict, save_config
from bug_hunter.core.database import (
    create_run, create_stage_result, get_engagement, get_run,
    list_bugs, update_engagement, update_run, update_stage_result,
)
from bug_hunter.core.events import event_manager
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext

logger = logging.getLogger(__name__)


# Stage ordering per engagement type
SOURCE_CODE_STAGES = [
    ("setup", 0),
    ("workload_divider", 1),
    ("bug_hunter", 2),
    ("deduplicator", 3),
    ("scope_validator", 4),
    ("strict_validator", 5),
    ("perfectionist", 6),
    ("strict_triager", 7),
    ("bug_chainer", 8),
]

BLACK_BOX_STAGES = [
    ("setup", 0),
    ("scope_enumerator", 1),
    ("bug_hunter", 2),
    ("deduplicator", 3),
    ("scope_validator", 4),
    ("strict_validator", 5),
    ("perfectionist", 6),
    ("strict_triager", 7),
    ("bug_chainer", 8),
]


class PipelineOrchestrator:
    """Orchestrates the full bug hunting pipeline."""

    def __init__(self, config: AppConfig, engagement_id: str):
        self.config = config
        self.engagement_id = engagement_id
        self.engagement = get_engagement(engagement_id)
        self._stages: dict[str, PipelineStage] = {}
        self._running = False
        self._current_run_id: Optional[str] = None

    def _get_stage_list(self) -> list[tuple[str, int]]:
        eng_type = self.engagement["type"]
        if eng_type == "source_code":
            stages = list(SOURCE_CODE_STAGES)
        else:
            stages = list(BLACK_BOX_STAGES)

        filtered = []
        for name, order in stages:
            if name == "workload_divider" and not self.config.workload_divider.enabled:
                continue
            if name == "deduplicator" and not self.config.deduplicator.enabled:
                if len(self.config.broad_bug_hunter.agents) <= 1:
                    continue
            filtered.append((name, order))

        return filtered

    def _get_run_dir(self, run_id: str) -> Path:
        output_dir = Path(self.config.pipeline.output_dir)
        return output_dir / "engagements" / self.engagement_id / "runs" / run_id

    def _get_cumulative_dir(self) -> Path:
        output_dir = Path(self.config.pipeline.output_dir)
        return output_dir / "engagements" / self.engagement_id / "cumulative"

    async def start_run(self, run_type: str = "initial",
                        rehunt_target: str = None) -> str:
        """Start a new pipeline run."""
        run = create_run(self.engagement_id, run_type, rehunt_target)
        run_id = run["id"]
        self._current_run_id = run_id

        run_dir = self._get_run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "logs").mkdir(exist_ok=True)

        cumulative_dir = self._get_cumulative_dir()
        cumulative_dir.mkdir(parents=True, exist_ok=True)

        config_path = run_dir.parent.parent / "config.yaml"
        if not config_path.exists():
            save_config(self.config, str(config_path))

        stages = self._get_stage_list()
        for stage_name, stage_order in stages:
            create_stage_result(run_id, stage_name, stage_order)

        update_run(run_id, status="running")
        await event_manager.emit_stage_update(
            self.engagement_id, run_id, "", "running",
            message="Pipeline started",
        )

        asyncio.create_task(self._execute_pipeline(run_id, run_type, rehunt_target))
        return run_id

    async def _execute_pipeline(self, run_id: str, run_type: str,
                                rehunt_target: str = None):
        """Execute the full pipeline sequentially."""
        self._running = True
        run_dir = self._get_run_dir(run_id)
        stages = self._get_stage_list()

        pipeline_state = {"completed_stages": [], "current_stage": None, "failed": False}

        if self.config.pipeline.resume:
            state_file = run_dir / "pipeline_state.json"
            if state_file.exists():
                with open(state_file) as f:
                    pipeline_state = json.load(f)

        context = StageContext(
            config=self.config,
            engagement_id=self.engagement_id,
            engagement=self.engagement,
            run_id=run_id,
            run_dir=str(run_dir),
            cumulative_dir=str(self._get_cumulative_dir()),
            run_type=run_type,
            rehunt_target=rehunt_target,
        )

        try:
            for stage_name, stage_order in stages:
                if stage_name in pipeline_state.get("completed_stages", []):
                    logger.info(f"Skipping completed stage: {stage_name}")
                    continue

                pipeline_state["current_stage"] = stage_name
                self._save_pipeline_state(run_dir, pipeline_state)
                update_run(run_id, current_stage=stage_name, pipeline_state=pipeline_state)

                await event_manager.emit_stage_update(
                    self.engagement_id, run_id, stage_name, "running",
                )

                stage_impl = self._get_stage(stage_name)
                if not stage_impl:
                    logger.warning(f"No implementation for stage: {stage_name}, skipping")
                    await self._mark_stage_skipped(run_id, stage_name)
                    pipeline_state["completed_stages"].append(stage_name)
                    continue

                success = await self._execute_stage_with_retry(
                    stage_impl, context, run_id, stage_name,
                )

                if success:
                    pipeline_state["completed_stages"].append(stage_name)
                    await event_manager.emit_stage_update(
                        self.engagement_id, run_id, stage_name, "completed",
                    )
                else:
                    pipeline_state["failed"] = True
                    await event_manager.emit_stage_update(
                        self.engagement_id, run_id, stage_name, "failed",
                    )

                self._save_pipeline_state(run_dir, pipeline_state)

            now = datetime.now(timezone.utc).isoformat()
            final_status = "completed" if not pipeline_state.get("failed") else "failed"
            update_run(run_id, status=final_status, completed_at=now)

            self._update_cumulative_state()

            summary = self._build_completion_summary(run_id)
            await event_manager.emit_completion(self.engagement_id, run_id, summary)

        except Exception as e:
            logger.exception(f"Pipeline failed: {e}")
            update_run(run_id, status="failed")
            await event_manager.emit_error(
                self.engagement_id, run_id, pipeline_state.get("current_stage", ""),
                str(e),
            )
        finally:
            self._running = False
            self._current_run_id = None

    async def _execute_stage_with_retry(
        self, stage: PipelineStage, context: StageContext,
        run_id: str, stage_name: str,
    ) -> bool:
        """Execute a stage with retry logic."""
        from bug_hunter.core.database import list_stage_results

        stage_results = list_stage_results(run_id)
        sr = next((s for s in stage_results if s["stage_name"] == stage_name), None)
        if not sr:
            return False

        sr_id = sr["id"]
        retry_limit = self.config.pipeline.retry_limit
        start_time = datetime.now(timezone.utc).isoformat()
        update_stage_result(sr_id, status="running", started_at=start_time)

        for attempt in range(1, retry_limit + 1):
            try:
                await event_manager.emit_log(
                    self.engagement_id, run_id, stage_name,
                    f"Attempt {attempt}/{retry_limit}",
                )

                result = await asyncio.wait_for(
                    stage.execute(context),
                    timeout=self.config.pipeline.subagent_timeout * 10,
                )

                if result.success:
                    end_time = datetime.now(timezone.utc).isoformat()
                    duration = int((
                        datetime.fromisoformat(end_time) -
                        datetime.fromisoformat(start_time)
                    ).total_seconds() * 1000)

                    update_stage_result(
                        sr_id, status="completed", completed_at=end_time,
                        duration_ms=duration, output_count=result.output_count,
                        input_count=result.input_count, cost_usd=result.cost_usd,
                        metadata=result.metadata,
                    )
                    return True
                else:
                    logger.warning(
                        f"Stage {stage_name} attempt {attempt} failed: {result.error}"
                    )
                    await event_manager.emit_error(
                        self.engagement_id, run_id, stage_name,
                        f"Attempt {attempt} failed: {result.error}",
                    )

            except asyncio.TimeoutError:
                logger.warning(f"Stage {stage_name} attempt {attempt} timed out")
                await event_manager.emit_error(
                    self.engagement_id, run_id, stage_name,
                    f"Attempt {attempt} timed out",
                )
            except Exception as e:
                logger.exception(f"Stage {stage_name} attempt {attempt} error: {e}")
                await event_manager.emit_error(
                    self.engagement_id, run_id, stage_name,
                    f"Attempt {attempt} error: {e}",
                )

        update_stage_result(
            sr_id, status="failed",
            error_message=f"Failed after {retry_limit} attempts",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return False

    def _get_stage(self, stage_name: str) -> Optional[PipelineStage]:
        """Get stage implementation by name."""
        if stage_name in self._stages:
            return self._stages[stage_name]

        from bug_hunter.pipeline.stages import registry
        stage_cls = registry.get(stage_name)
        if stage_cls:
            stage = stage_cls()
            self._stages[stage_name] = stage
            return stage
        return None

    async def _mark_stage_skipped(self, run_id: str, stage_name: str):
        from bug_hunter.core.database import list_stage_results
        stage_results = list_stage_results(run_id)
        sr = next((s for s in stage_results if s["stage_name"] == stage_name), None)
        if sr:
            update_stage_result(sr["id"], status="skipped")
        await event_manager.emit_stage_update(
            self.engagement_id, run_id, stage_name, "skipped",
        )

    def _save_pipeline_state(self, run_dir: Path, state: dict):
        state_file = run_dir / "pipeline_state.json"
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)

    def _update_cumulative_state(self):
        """Update cumulative engagement state across all runs."""
        cumulative_dir = self._get_cumulative_dir()

        all_confirmed = list_bugs(self.engagement_id, status="confirmed")
        all_cannot_validate = list_bugs(self.engagement_id, status="cannot_validate")
        all_informational = list_bugs(self.engagement_id, status="informational")

        with open(cumulative_dir / "all_confirmed_bugs.json", "w") as f:
            json.dump([b["bug_data"] for b in all_confirmed], f, indent=2)

        with open(cumulative_dir / "all_cannot_validate.json", "w") as f:
            json.dump([b["bug_data"] for b in all_cannot_validate], f, indent=2)

        with open(cumulative_dir / "intelligence.json", "w") as f:
            json.dump([b["bug_data"] for b in all_informational], f, indent=2)

        total_cost = sum(
            r.get("cost_usd", 0)
            for r in __import__("bug_hunter.core.database", fromlist=["list_runs"]).list_runs(self.engagement_id)
        )
        update_engagement(self.engagement_id, cost_total_usd=total_cost)

    def _build_completion_summary(self, run_id: str) -> dict:
        from bug_hunter.core.database import list_stage_results
        stages = list_stage_results(run_id)
        confirmed = list_bugs(self.engagement_id, status="confirmed")
        cannot_validate = list_bugs(self.engagement_id, status="cannot_validate")

        return {
            "run_id": run_id,
            "stages_completed": sum(1 for s in stages if s["status"] == "completed"),
            "stages_failed": sum(1 for s in stages if s["status"] == "failed"),
            "stages_skipped": sum(1 for s in stages if s["status"] == "skipped"),
            "confirmed_bugs": len(confirmed),
            "cannot_validate": len(cannot_validate),
            "total_stages": len(stages),
        }
