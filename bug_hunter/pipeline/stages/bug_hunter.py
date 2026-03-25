"""Bug Hunter stage — free-form vulnerability hunting with progress tracking.

The Bug Hunter is a single agent that:
1. Reads the Scoper's attack surface map
2. Hunts for bugs freely, following interesting leads
3. Updates structured files to track progress:
   - attack_surfaces.json — marks surfaces as scanned, adds new ones found
   - BUGS.json — documents all bugs found with root cause, impact, PoC, validation status
4. Can run for N iterations — each iteration reads previous progress and continues
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from bug_hunter.core.cli_wrapper import CLIResult, StreamEvent, run_claude, run_codex
from bug_hunter.core.database import create_bug
from bug_hunter.core.events import event_manager
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register
from bug_hunter.utils.schema_validator import validate_findings_list

logger = logging.getLogger(__name__)
AGENTS_DIR = Path(__file__).parent.parent.parent.parent / "agents"


@register
class BugHunterStage(PipelineStage):

    @property
    def name(self) -> str:
        return "bug_hunter"

    async def execute(self, context: StageContext) -> StageResult:
        stage_dir = self.get_stage_dir(context)
        eng_config = context.engagement["config"]
        eng_type = context.engagement["type"]
        hunter_config = context.config.bug_hunter
        infra_config = eng_config.get("engagement", {}).get("infra_config", "")

        # Load scope from Scoper stage
        scope_data = self.read_previous_output(context, "scoper", "scope.json")
        if not scope_data:
            return StageResult(success=False, error="No scope data from Scoper stage")

        # Get source path
        setup_data = self.read_previous_output(context, "setup", "setup.json")
        source_path = ""
        if setup_data and "source" in setup_data:
            source_path = setup_data["source"]["local_path"]
        if not source_path:
            source_path = eng_config.get("engagement", {}).get("source_path", "")

        # Initialize progress files if first iteration
        attack_surfaces_file = os.path.join(stage_dir, "attack_surfaces.json")
        bugs_file = os.path.join(stage_dir, "BUGS.json")

        if not os.path.exists(attack_surfaces_file):
            with open(attack_surfaces_file, "w") as f:
                json.dump(scope_data.get("attack_surfaces", []), f, indent=2)

        if not os.path.exists(bugs_file):
            with open(bugs_file, "w") as f:
                json.dump([], f, indent=2)

        with open(attack_surfaces_file) as f:
            attack_surfaces = json.load(f)
        with open(bugs_file) as f:
            existing_bugs = json.load(f)

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Bug hunting iteration — {len(attack_surfaces)} surfaces, {len(existing_bugs)} bugs found so far",
        )

        agents = hunter_config.agents
        total_cost = 0.0
        total_usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        all_new_bugs = []

        agent_stats = {agent: {"succeeded": 0, "failed": 0, "running": 0, "total": 1} for agent in agents}

        async def run_agent(agent_name: str):
            nonlocal total_cost
            agent_stats[agent_name]["running"] = 1
            await event_manager.emit("agent_progress", context.engagement_id, context.run_id, self.name, {
                "agent": agent_name, "status": "running", "total_chunks": 1,
                "succeeded": 0, "failed": 0, "running": 1,
            })

            result = await self._run_hunter(
                context, agent_name, scope_data, attack_surfaces,
                existing_bugs, source_path, infra_config, eng_type, stage_dir,
            )
            total_cost += result.cost_usd
            if result.usage:
                for k in total_usage:
                    total_usage[k] += result.usage.get(k, 0)
            agent_stats[agent_name]["running"] = 0

            if result.success and result.result:
                from bug_hunter.utils.result_parser import parse_agent_result
                raw_file = os.path.join(stage_dir, f"raw_output_{agent_name}.md")
                data = parse_agent_result(
                    result.result, ["bugs", "attack_surfaces"],
                    f"bug_hunter/{agent_name}", save_raw_to=raw_file,
                )
                if not data.get("bugs") and isinstance(result.result, str):
                    await event_manager.emit_log(
                        context.engagement_id, context.run_id, self.name,
                        f"[{agent_name}] Returned text instead of JSON — saved to raw_output_{agent_name}.md",
                    )
                new_bugs = data.get("bugs", [])
                updated_surfaces = data.get("attack_surfaces", [])

                run_prefix = context.run_id[:8]
                for i, bug in enumerate(new_bugs):
                    if "id" not in bug:
                        bug["id"] = f"{run_prefix}/{agent_name}-{len(existing_bugs) + i:03d}"
                    bug["found_by"] = [agent_name]

                if updated_surfaces:
                    self._merge_attack_surfaces(attack_surfaces_file, updated_surfaces)

                agent_stats[agent_name]["succeeded"] = 1
                await event_manager.emit("agent_progress", context.engagement_id, context.run_id, self.name, {
                    "agent": agent_name, "status": "done",
                    "bugs_found": len(new_bugs), "total_chunks": 1,
                    "succeeded": 1, "failed": 0, "running": 0,
                })
                return new_bugs
            else:
                agent_stats[agent_name]["failed"] = 1
                await event_manager.emit("agent_progress", context.engagement_id, context.run_id, self.name, {
                    "agent": agent_name, "status": "failed",
                    "error": result.error[:200] if result.error else "", "total_chunks": 1,
                    "succeeded": 0, "failed": 1, "running": 0,
                })
                logger.warning(f"Bug hunter ({agent_name}) failed: {result.error}")
                return []

        tasks = [run_agent(agent) for agent in agents]
        for coro in asyncio.as_completed(tasks):
            new_bugs = await coro
            all_new_bugs.extend(new_bugs)

        # Append new bugs to BUGS.json
        combined_bugs = existing_bugs + all_new_bugs
        with open(bugs_file, "w") as f:
            json.dump(combined_bugs, f, indent=2)

        # Validate and persist to DB
        quarantine_dir = os.path.join(stage_dir, "quarantined")
        valid_bugs, quarantined = validate_findings_list(all_new_bugs, quarantine_dir)

        self.write_output(context, "all_findings.json", valid_bugs)
        self.write_output(context, "all_summaries.json", [scope_data.get("architecture", {})])

        for bug in valid_bugs:
            create_bug(context.engagement_id, context.run_id, bug)

        succeeded = sum(1 for s in agent_stats.values() if s["succeeded"])
        failed = sum(1 for s in agent_stats.values() if s["failed"])

        return StageResult(
            success=True,
            input_count=len(attack_surfaces),
            output_count=len(valid_bugs),
            cost_usd=total_cost,
            metadata={
                "new_bugs_found": len(all_new_bugs),
                "total_bugs_cumulative": len(combined_bugs),
                "quarantined": len(quarantined),
                "agents_succeeded": succeeded,
                "agents_failed": failed,
                "coverage_ratio": round(succeeded / len(agents), 2) if agents else 0,
                "usage": total_usage,
            },
        )

    async def _run_hunter(self, context: StageContext, agent_name: str,
                          scope_data: dict, attack_surfaces: list,
                          existing_bugs: list, source_path: str,
                          infra_config: str, eng_type: str, stage_dir: str) -> CLIResult:
        """Run a single bug hunter agent."""
        scope_json = json.dumps(scope_data, indent=2)[:20000]
        surfaces_json = json.dumps(attack_surfaces, indent=2)[:15000]
        bugs_summary = json.dumps([{
            "id": b.get("id"), "vuln_type": b.get("vuln_type"),
            "source_file": b.get("source_file"), "validated": b.get("validated"),
        } for b in existing_bugs], indent=2)[:10000] if existing_bugs else "[]"

        base_instructions = f"""You are a security researcher performing a thorough vulnerability assessment.

{"SOURCE CODE ROOT: " + source_path if eng_type == "source_code" else ""}
{"INFRASTRUCTURE ACCESS:" + chr(10) + infra_config if infra_config else ""}

APPLICATION CONTEXT:
{scope_json}

ATTACK SURFACES TO INVESTIGATE:
{surfaces_json}

BUGS ALREADY FOUND (do not duplicate):
{bugs_summary}"""

        if agent_name == "codex" or agent_name.startswith("codex"):
            methodology = """
METHODOLOGY — BE THOROUGH:
1. For EACH attack surface marked "not_scanned", read the actual source files listed
2. Do NOT skim — read every function, trace every data flow from user input to dangerous operation
3. For each potential bug:
   a. Identify the exact file and line where the vulnerability exists
   b. Trace the full path from user-controlled input to the vulnerable sink
   c. Check what sanitization or validation exists in the path
   d. Write a concrete PoC (Python script with requests library) if infrastructure is available
   e. Attempt to execute the PoC and record the result
4. Look for logic bugs across files — check if auth decorators are missing, if validation is inconsistent
5. After investigating each surface, mark it "scanned" with notes on what you found
6. Discover NEW attack surfaces not in the original list
7. Do NOT stop after finding a few bugs — continue scanning ALL surfaces"""
        else:
            methodology = """
INSTRUCTIONS:
1. Focus on surfaces marked "not_scanned" first, then re-examine "scanned" ones
2. For source code: read the actual code, trace data flows, identify vulnerabilities
3. For each bug found, provide root cause, security impact, PoC, and validation status
4. If you discover NEW attack surfaces, include them in your output
5. Mark each surface you reviewed with status "scanned"
6. Be thorough — prioritize high-impact findings"""

        prompt = f"""{base_instructions}
{methodology}

CRITICAL — OUTPUT FORMAT:
You MUST output a JSON object at the end. Do NOT output a prose report.
The JSON must have this exact structure:
{{
  "bugs": [
    {{
      "id": "unique-id",
      "source_file": "path/to/file",
      "line_range": "10-25",
      "vuln_class": "CWE-89",
      "vuln_type": "SQL Injection",
      "description": "Detailed description",
      "reasoning": "Why this is exploitable",
      "confidence": "high",
      "root_cause": "What is wrong in the code",
      "security_impact": "What an attacker can achieve",
      "validated": true,
      "poc": {{
        "language": "python",
        "code": "the PoC code",
        "execution_result": "success or failure",
        "output": "proof of exploitation"
      }}
    }}
  ],
  "attack_surfaces": [
    {{
      "id": "surface-001",
      "name": "Updated surface name",
      "status": "scanned",
      "findings_notes": "What was found or why it's clean"
    }}
  ]
}}"""

        if eng_type == "source_code":
            agent_file = str(AGENTS_DIR / "source_code" / "bug_hunter.md")
        else:
            agent_file = str(AGENTS_DIR / "black_box" / "bug_hunter.md")

        def on_event(event: StreamEvent):
            text = ""
            if event.type == "assistant":
                content = event.data.get("content", "")
                if isinstance(content, list):
                    text = " ".join(
                        block.get("text", "") for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                elif isinstance(content, str):
                    text = content
            elif event.type == "item.completed":
                item = event.data.get("item", {})
                if item.get("type") == "agent_message":
                    text = item.get("text", "")
            if text:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(event_manager.emit_agent_stream(
                        context.engagement_id, context.run_id, self.name,
                        agent_name, text[:500],
                    ))
                except RuntimeError:
                    pass

        record_dir, record_meta = self.prepare_agent_run(
            context, agent_name, "bug_hunt",
            {"model": context.config.models.bug_hunter_subagent, "engagement_type": eng_type},
        )

        if agent_name == "claude" or agent_name.startswith("claude"):
            return await run_claude(
                prompt=prompt,
                agent_file=agent_file,
                model=context.config.models.bug_hunter_subagent,
                cwd=source_path if eng_type == "source_code" else None,
                timeout=context.config.pipeline.subagent_timeout,
                on_event=on_event,
                record_dir=record_dir,
                record_metadata=record_meta,
            )
        elif agent_name == "codex" or agent_name.startswith("codex"):
            return await run_codex(
                prompt=prompt,
                model=context.config.bug_hunter.codex_model,
                cwd=source_path if eng_type == "source_code" else None,
                timeout=context.config.pipeline.subagent_timeout,
                on_event=on_event,
                record_dir=record_dir,
                record_metadata=record_meta,
            )
        else:
            return CLIResult(success=False, error=f"Unknown agent: {agent_name}")

    def _merge_attack_surfaces(self, surfaces_file: str, new_surfaces: list):
        """Merge updated attack surfaces into the existing file."""
        try:
            with open(surfaces_file) as f:
                existing = json.load(f)

            existing_ids = {s.get("id") for s in existing}
            for surface in new_surfaces:
                sid = surface.get("id")
                if sid and sid in existing_ids:
                    for i, ex in enumerate(existing):
                        if ex.get("id") == sid:
                            existing[i].update(surface)
                            break
                else:
                    existing.append(surface)

            with open(surfaces_file, "w") as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to merge attack surfaces: {e}")
