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

LIMIT_ERROR_PATTERNS = (
    "session limit",
    "usage limit",
    "rate limit",
    "too many requests",
    "exceeded your current quota",
    "quota",
    "billing",
    "credit balance",
)


# Stage ordering — shared pipeline for both engagement types
# Scoper handles both source code mapping and black box recon
PIPELINE_STAGES = [
    ("setup", 0),
    ("scoper", 1),
    ("bug_hunter", 2),
    ("deduplicator", 3),
    ("scope_validator", 4),
    ("strict_validator", 5),
    ("perfectionist", 6),
    ("strict_triager", 7),
    ("bug_chainer", 8),
    ("summarizer", 9),
]

# Keep legacy constants for backward compatibility
SOURCE_CODE_STAGES = PIPELINE_STAGES
BLACK_BOX_STAGES = PIPELINE_STAGES


class PipelineOrchestrator:
    """Orchestrates the full bug hunting pipeline."""

    def __init__(self, config: AppConfig, engagement_id: str):
        self.config = config
        self.engagement_id = engagement_id
        self.engagement = get_engagement(engagement_id)
        self._stages: dict[str, PipelineStage] = {}
        self._running = False
        self._cancelled = False
        self._paused = False
        self._pause_reason = ""
        self._current_run_id: Optional[str] = None
        self._current_task: Optional[asyncio.Task] = None

    def _get_stage_list(self) -> list[tuple[str, int]]:
        stages = list(PIPELINE_STAGES)

        filtered = []
        for name, order in stages:
            if name == "deduplicator" and not self.config.deduplicator.enabled:
                if len(self.config.bug_hunter.agents) <= 1:
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
        """Start a new pipeline run.

        Creates the run with status='running' immediately so the unique
        partial index catches concurrent starts at INSERT time — before
        any stage rows or directories are created.
        """
        # This INSERT will raise IntegrityError if another run is already
        # 'running' for this engagement, thanks to idx_one_active_run_per_engagement.
        run = create_run(self.engagement_id, run_type, rehunt_target, status="running")
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

        # For rehunts: copy setup/scoper output from the latest completed run
        # so the Bug Hunter can read scope.json without re-running those stages
        if run_type == "rehunt":
            self._copy_prior_stage_outputs(run_id, run_dir, ["setup", "scoper"])

        await event_manager.emit_stage_update(
            self.engagement_id, run_id, "", "running",
            message="Pipeline started",
        )

        self._cancelled = False
        self._paused = False
        self._pause_reason = ""
        self._current_task = asyncio.create_task(self._execute_pipeline(run_id, run_type, rehunt_target))
        return run_id

    async def resume_run(self, run_id: str) -> bool:
        """Resume a paused, failed, or cancelled pipeline run from its saved state."""
        run = get_run(run_id)
        if not run or run["engagement_id"] != self.engagement_id:
            return False
        if run["status"] not in ("paused", "failed", "cancelled"):
            return False

        self._current_run_id = run_id
        self._cancelled = False
        self._paused = False
        self._pause_reason = ""

        pipeline_state = run.get("pipeline_state") or {}
        current_stage = pipeline_state.get("current_stage")
        if current_stage and current_stage not in pipeline_state.get("completed_stages", []):
            self._reset_stage_result(run_id, current_stage)

        update_run(run_id, status="running", completed_at=None)
        await event_manager.emit_stage_update(
            self.engagement_id, run_id, "", "running",
            message=f"Pipeline resumed from {current_stage or 'start'}",
        )
        self._current_task = asyncio.create_task(
            self._execute_pipeline(run_id, run["run_type"], run.get("rehunt_target")),
        )
        return True

    async def cancel_run(self):
        """Cancel the currently running pipeline."""
        if not self._running or not self._current_run_id:
            return False
        self._cancelled = True
        self._paused = False
        self._pause_reason = ""
        run_id = self._current_run_id
        logger.info(f"Cancelling run {run_id}")
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        return True

    async def pause_run(self, reason: str = "Run paused by user") -> bool:
        """Pause the currently running pipeline and allow later resume."""
        if not self._running or not self._current_run_id:
            return False
        self._paused = True
        self._cancelled = False
        self._pause_reason = reason
        logger.info(f"Pausing run {self._current_run_id}: {reason}")
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        return True

    async def _execute_pipeline(self, run_id: str, run_type: str,
                                rehunt_target: str = None):
        """Execute the full pipeline sequentially."""
        self._running = True
        run_dir = self._get_run_dir(run_id)
        stages = self._get_stage_list()

        pipeline_state = {"completed_stages": [], "current_stage": None, "failed": False}

        # For rehunts, pre-mark setup/scoper as completed (outputs already copied)
        if run_type == "rehunt":
            pipeline_state["completed_stages"] = [s for s in self.REHUNT_SKIP_STAGES
                                                   if any(name == s for name, _ in stages)]

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
                if self._cancelled:
                    logger.info("Pipeline cancelled by user")
                    break

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

                outcome = await self._execute_stage_with_retry(
                    stage_impl, context, run_id, stage_name,
                )

                if outcome == "completed":
                    pipeline_state["completed_stages"].append(stage_name)
                    await event_manager.emit_stage_update(
                        self.engagement_id, run_id, stage_name, "completed",
                    )
                elif outcome == "paused":
                    pipeline_state["paused"] = True
                    pipeline_state["paused_stage"] = stage_name
                    self._save_pipeline_state(run_dir, pipeline_state)
                    await event_manager.emit_stage_update(
                        self.engagement_id, run_id, stage_name, "paused",
                        message=self._pause_reason or "Run paused",
                    )
                    break
                else:
                    pipeline_state["failed"] = True
                    pipeline_state["failed_stage"] = stage_name
                    await event_manager.emit_stage_update(
                        self.engagement_id, run_id, stage_name, "failed",
                    )
                    await event_manager.emit_error(
                        self.engagement_id, run_id, stage_name,
                        f"Stage '{stage_name}' failed after retries. Pipeline halted.",
                    )
                    break

                self._save_pipeline_state(run_dir, pipeline_state)

            now = datetime.now(timezone.utc).isoformat()
            if self._paused or pipeline_state.get("paused"):
                final_status = "paused"
            elif self._cancelled:
                final_status = "cancelled"
            elif pipeline_state.get("failed"):
                final_status = "failed"
            else:
                final_status = "completed"
            update_run(
                run_id,
                status=final_status,
                completed_at=now if final_status in ("completed", "failed", "cancelled") else None,
                pipeline_state=pipeline_state,
            )

            self._update_cumulative_state()

            if final_status != "paused":
                summary = self._build_completion_summary(run_id)
                await event_manager.emit_completion(self.engagement_id, run_id, summary)

        except asyncio.CancelledError:
            logger.info(f"Pipeline task cancelled for run {run_id}")
            if self._paused:
                pipeline_state["paused"] = True
                pipeline_state["paused_stage"] = pipeline_state.get("current_stage")
                self._save_pipeline_state(run_dir, pipeline_state)
                self._reset_stage_result(run_id, pipeline_state.get("current_stage"))
                update_run(run_id, status="paused", completed_at=None, pipeline_state=pipeline_state)
                await event_manager.emit_stage_update(
                    self.engagement_id, run_id, "", "paused",
                    message=self._pause_reason or "Run paused",
                )
            else:
                now = datetime.now(timezone.utc).isoformat()
                update_run(run_id, status="cancelled", completed_at=now, pipeline_state=pipeline_state)
                await event_manager.emit_stage_update(
                    self.engagement_id, run_id, "", "cancelled",
                    message="Run cancelled by user",
                )
        except Exception as e:
            logger.exception(f"Pipeline failed: {e}")
            update_run(run_id, status="failed", pipeline_state=pipeline_state)
            await event_manager.emit_error(
                self.engagement_id, run_id, pipeline_state.get("current_stage", ""),
                str(e),
            )
        finally:
            self._running = False
            self._cancelled = False
            self._paused = False
            self._pause_reason = ""
            self._current_run_id = None
            self._current_task = None

    async def _execute_stage_with_retry(
        self, stage: PipelineStage, context: StageContext,
        run_id: str, stage_name: str,
    ) -> str:
        """Execute a stage with retry logic."""
        from bug_hunter.core.database import list_stage_results

        stage_results = list_stage_results(run_id)
        sr = next((s for s in stage_results if s["stage_name"] == stage_name), None)
        if not sr:
            return "failed"

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
                    return "completed"
                else:
                    logger.warning(
                        f"Stage {stage_name} attempt {attempt} failed: {result.error}"
                    )
                    if self._should_pause_for_error(result.error):
                        self._pause_reason = (
                            f"Paused after {stage_name} hit an agent/session limit. "
                            "Resume the run after your Claude/Codex session is available again."
                        )
                        self._reset_stage_result(run_id, stage_name)
                        return "paused"
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
                if self._should_pause_for_error(str(e)):
                    self._pause_reason = (
                        f"Paused after {stage_name} hit an agent/session limit. "
                        "Resume the run after your Claude/Codex session is available again."
                    )
                    self._reset_stage_result(run_id, stage_name)
                    return "paused"
                await event_manager.emit_error(
                    self.engagement_id, run_id, stage_name,
                    f"Attempt {attempt} error: {e}",
                )

        update_stage_result(
            sr_id, status="failed",
            error_message=f"Failed after {retry_limit} attempts",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return "failed"

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

    def _reset_stage_result(self, run_id: str, stage_name: Optional[str]):
        """Reset the current stage so it can be replayed on resume."""
        if not stage_name:
            return
        from bug_hunter.core.database import list_stage_results

        stage_results = list_stage_results(run_id)
        sr = next((s for s in stage_results if s["stage_name"] == stage_name), None)
        if sr:
            update_stage_result(
                sr["id"],
                status="pending",
                started_at=None,
                completed_at=None,
                duration_ms=None,
                error_message=None,
                metadata=None,
            )

    def _should_pause_for_error(self, message: str) -> bool:
        text = (message or "").lower()
        return any(pattern in text for pattern in LIMIT_ERROR_PATTERNS)

    # Stages to skip on rehunt — setup and scoper already ran in a prior run
    REHUNT_SKIP_STAGES = {"setup", "scoper"}

    def _copy_prior_stage_outputs(self, run_id: str, run_dir: Path, stage_names: list[str]):
        """Copy stage output dirs from the latest completed run into this run.

        Also marks those stages as completed in the DB so the pipeline skips them.
        """
        import shutil
        from bug_hunter.core.database import list_runs as db_list_runs, list_stage_results

        # Find the latest completed run (not this one)
        prior_runs = [
            r for r in db_list_runs(self.engagement_id)
            if r["id"] != run_id and r["status"] in ("completed", "failed", "paused")
        ]
        if not prior_runs:
            logger.warning("No prior completed run found — rehunt will run full pipeline")
            return

        prior_run = prior_runs[-1]
        prior_dir = self._get_run_dir(prior_run["id"])

        for stage_name in stage_names:
            # Find stage order
            stage_order = None
            for name, order in PIPELINE_STAGES:
                if name == stage_name:
                    stage_order = order
                    break
            if stage_order is None:
                continue

            src_dir = prior_dir / f"{stage_order:02d}_{stage_name}"
            dst_dir = run_dir / f"{stage_order:02d}_{stage_name}"

            if src_dir.exists() and not dst_dir.exists():
                shutil.copytree(str(src_dir), str(dst_dir))
                logger.info(f"Copied {stage_name} output from run {prior_run['id'][:8]}")

            # Mark stage as completed in DB
            stage_results = list_stage_results(run_id)
            sr = next((s for s in stage_results if s["stage_name"] == stage_name), None)
            if sr:
                update_stage_result(sr["id"], status="completed")

    def _save_pipeline_state(self, run_dir: Path, state: dict):
        state_file = run_dir / "pipeline_state.json"
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)

    def _update_cumulative_state(self):
        """Update cumulative engagement state across all runs."""
        from bug_hunter.core.database import list_runs as db_list_runs, list_stage_results

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

        # Calculate costs from stage results (the source of truth)
        total_cost = 0.0
        for run in db_list_runs(self.engagement_id):
            run_stages = list_stage_results(run["id"])
            run_cost = sum(s.get("cost_usd", 0) or 0 for s in run_stages)
            if run_cost > 0:
                update_run(run["id"], cost_usd=run_cost)
            total_cost += run_cost
        update_engagement(self.engagement_id, cost_total_usd=total_cost)

    def _build_completion_summary(self, run_id: str) -> dict:
        from bug_hunter.core.database import list_stage_results

        stages = list_stage_results(run_id)

        # Per-run counts
        run_confirmed = list_bugs(self.engagement_id, status="confirmed", run_id=run_id)
        run_cannot_validate = list_bugs(self.engagement_id, status="cannot_validate", run_id=run_id)
        run_triage_failed = list_bugs(self.engagement_id, status="triage_failed", run_id=run_id)

        # Cumulative counts
        all_confirmed = list_bugs(self.engagement_id, status="confirmed")
        all_cannot_validate = list_bugs(self.engagement_id, status="cannot_validate")

        return {
            "run_id": run_id,
            "stages_completed": sum(1 for s in stages if s["status"] == "completed"),
            "stages_failed": sum(1 for s in stages if s["status"] == "failed"),
            "stages_skipped": sum(1 for s in stages if s["status"] == "skipped"),
            "total_stages": len(stages),
            "run_confirmed_bugs": len(run_confirmed),
            "run_cannot_validate": len(run_cannot_validate),
            "run_triage_failed": len(run_triage_failed),
            "cumulative_confirmed_bugs": len(all_confirmed),
            "cumulative_cannot_validate": len(all_cannot_validate),
        }
