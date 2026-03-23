"""Bug Hunter stage — source code audit (Phase 1 broad sweep + Phase 2 logic bugs)
and black box pentesting."""

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
from bug_hunter.utils.schema_validator import validate_findings_list
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)

AGENTS_DIR = Path(__file__).parent.parent.parent.parent / "agents"


@register
class BugHunterStage(PipelineStage):

    @property
    def name(self) -> str:
        return "bug_hunter"

    async def execute(self, context: StageContext) -> StageResult:
        eng_type = context.engagement["type"]
        if eng_type == "source_code":
            return await self._execute_source_code(context)
        else:
            return await self._execute_black_box(context)

    async def _execute_source_code(self, context: StageContext) -> StageResult:
        """Source code audit: map codebase, deploy subagents, run Phase 1 + Phase 2."""
        stage_dir = self.get_stage_dir(context)
        eng_config = context.engagement["config"]
        hunter_config = context.config.broad_bug_hunter

        setup_data = self.read_previous_output(context, "setup", "setup.json")
        source_path = ""
        if setup_data and "source" in setup_data:
            source_path = setup_data["source"]["local_path"]
        if not source_path:
            source_path = eng_config.get("engagement", {}).get("source_path", "")

        if not source_path or not os.path.isdir(source_path):
            return StageResult(success=False, error=f"Source path not found: {source_path}")

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Mapping codebase at: {source_path}",
        )

        codebase_map = await self._map_codebase(source_path, hunter_config)
        self.write_output(context, "codebase_map.json", codebase_map)

        chunks = self._split_into_chunks(codebase_map, hunter_config.context_budget)
        total_chunks = len(chunks)

        await event_manager.emit_progress(
            context.engagement_id, context.run_id, self.name,
            0, total_chunks, f"Phase 1: Deploying {total_chunks} subagents",
        )

        all_findings = []
        all_summaries = []
        total_cost = 0.0

        phase1_dir = os.path.join(stage_dir, "phase1")
        os.makedirs(phase1_dir, exist_ok=True)

        semaphore = asyncio.Semaphore(len(hunter_config.agents) * 3)

        # Per-agent tracking
        agent_stats: dict[str, dict] = {
            agent: {"succeeded": 0, "failed": 0, "running": 0, "total": 0}
            for agent in hunter_config.agents
        }
        # Count total per agent
        for chunk in chunks:
            for agent in hunter_config.agents:
                agent_stats[agent]["total"] += 1

        async def emit_agent_progress(agent_name: str, status: str, chunk_idx: int = -1):
            stats = agent_stats[agent_name]
            await event_manager.emit(
                "agent_progress", context.engagement_id, context.run_id, self.name,
                {
                    "agent": agent_name,
                    "status": status,
                    "current_chunk": chunk_idx if status == "running" else None,
                    "total_chunks": stats["total"],
                    "succeeded": stats["succeeded"],
                    "failed": stats["failed"],
                    "running": stats["running"],
                },
            )

        async def run_chunk(chunk_idx: int, chunk: dict, agent_name: str):
            nonlocal total_cost
            async with semaphore:
                agent_stats[agent_name]["running"] += 1
                await emit_agent_progress(agent_name, "running", chunk_idx)

                result = await self._run_subagent(
                    context, chunk, agent_name, source_path,
                )
                total_cost += result.cost_usd
                agent_stats[agent_name]["running"] -= 1

                chunk_file = f"chunk_{chunk_idx:03d}_{agent_name}"
                if result.success and result.result:
                    findings = result.result.get("findings", [])
                    summary = result.result.get("functionality_summary", {})

                    run_prefix = context.run_id[:8]
                    for i, finding in enumerate(findings):
                        if "id" not in finding:
                            finding["id"] = f"{run_prefix}/bug-{chunk_idx:03d}-{i:03d}"
                        finding["found_by"] = [agent_name]

                    with open(os.path.join(phase1_dir, f"{chunk_file}_findings.json"), "w") as f:
                        json.dump(findings, f, indent=2)
                    with open(os.path.join(phase1_dir, f"{chunk_file}_summary.json"), "w") as f:
                        json.dump(summary, f, indent=2)

                    agent_stats[agent_name]["succeeded"] += 1
                    await emit_agent_progress(agent_name, "chunk_done", chunk_idx)
                    return findings, summary
                else:
                    logger.warning(f"Chunk {chunk_idx} ({agent_name}) failed: {result.error}")
                    with open(os.path.join(phase1_dir, f"{chunk_file}_error.json"), "w") as f:
                        json.dump({"error": result.error, "raw": result.raw_output[:2000]}, f, indent=2)

                    agent_stats[agent_name]["failed"] += 1
                    await event_manager.emit_log(
                        context.engagement_id, context.run_id, self.name,
                        f"[{agent_name}] Chunk {chunk_idx} failed: {result.error[:200]}",
                    )
                    await emit_agent_progress(agent_name, "chunk_failed", chunk_idx)
                    return [], {}

        tasks = []
        for chunk_idx, chunk in enumerate(chunks):
            for agent_name in hunter_config.agents:
                tasks.append(run_chunk(chunk_idx, chunk, agent_name))

        completed = 0
        succeeded = 0
        failed = 0
        for coro in asyncio.as_completed(tasks):
            findings, summary = await coro
            all_findings.extend(findings)
            if summary:
                all_summaries.append(summary)
                succeeded += 1
            else:
                failed += 1
            completed += 1
            await event_manager.emit_progress(
                context.engagement_id, context.run_id, self.name,
                completed, len(tasks), f"Phase 1: {completed}/{len(tasks)} subagents complete",
            )

        coverage_ratio = succeeded / len(tasks) if tasks else 0
        is_degraded = coverage_ratio < 0.5

        if is_degraded:
            await event_manager.emit_error(
                context.engagement_id, context.run_id, self.name,
                f"Degraded coverage: only {succeeded}/{len(tasks)} subagents succeeded ({coverage_ratio:.0%})",
            )

        if hunter_config.phase2_enabled and all_summaries:
            await event_manager.emit_log(
                context.engagement_id, context.run_id, self.name,
                "Phase 2: Analyzing cross-component interactions",
            )
            phase2_findings = await self._run_phase2(
                context, all_summaries, source_path, stage_dir,
            )
            all_findings.extend(phase2_findings)

        quarantine_dir = os.path.join(stage_dir, "quarantined")
        valid_findings, quarantined = validate_findings_list(all_findings, quarantine_dir)
        if quarantined:
            await event_manager.emit_log(
                context.engagement_id, context.run_id, self.name,
                f"Quarantined {len(quarantined)} malformed findings (see quarantined/)",
            )

        self.write_output(context, "all_findings.json", valid_findings)
        self.write_output(context, "all_summaries.json", all_summaries)

        for finding in valid_findings:
            create_bug(context.engagement_id, context.run_id, finding)

        return StageResult(
            success=True,
            input_count=len(chunks),
            output_count=len(valid_findings),
            cost_usd=total_cost,
            metadata={
                "phase1_chunks": len(chunks),
                "quarantined": len(quarantined),
                "phase2_enabled": hunter_config.phase2_enabled,
                "subagents_succeeded": succeeded,
                "subagents_failed": failed,
                "coverage_ratio": round(coverage_ratio, 2),
                "degraded": is_degraded,
            },
        )

    async def _execute_black_box(self, context: StageContext) -> StageResult:
        """Black box pentest: deploy per-target bug hunters."""
        stage_dir = self.get_stage_dir(context)
        eng_config = context.engagement["config"]

        enum_data = self.read_previous_output(context, "scope_enumerator", "attack_surface_map.json")
        if not enum_data:
            return StageResult(success=False, error="No attack surface map from scope enumerator")

        targets = enum_data.get("targets", [])
        if not targets:
            return StageResult(success=False, error="No targets found by scope enumerator")

        infra_config = eng_config.get("engagement", {}).get("infra_config", "")
        all_findings = []
        total_cost = 0.0

        semaphore = asyncio.Semaphore(context.config.pipeline.max_concurrent_infra_agents)

        async def hunt_target(target_idx: int, target: dict):
            """Returns (findings_list, agent_succeeded)."""
            nonlocal total_cost
            async with semaphore:
                target_dir = os.path.join(stage_dir, f"target_{target_idx:03d}")
                os.makedirs(target_dir, exist_ok=True)

                result = await self._run_black_box_hunter(
                    context, target, infra_config, target_dir,
                )
                total_cost += result.cost_usd

                if result.success:
                    findings = []
                    if result.result:
                        findings = result.result if isinstance(result.result, list) else result.result.get("findings", [])
                    run_prefix = context.run_id[:8]
                    for i, finding in enumerate(findings):
                        if "id" not in finding:
                            finding["id"] = f"{run_prefix}/bb-{target_idx:03d}-{i:03d}"
                        finding["found_by"] = ["claude"]

                    with open(os.path.join(target_dir, "findings.json"), "w") as f:
                        json.dump(findings, f, indent=2)
                    return findings, True
                else:
                    with open(os.path.join(target_dir, "error.json"), "w") as f:
                        json.dump({"error": result.error}, f, indent=2)
                    return [], False

        tasks = [hunt_target(i, t) for i, t in enumerate(targets)]

        completed = 0
        succeeded = 0
        failed = 0
        for coro in asyncio.as_completed(tasks):
            findings, agent_ok = await coro
            all_findings.extend(findings)
            if agent_ok:
                succeeded += 1
            else:
                failed += 1
            completed += 1
            await event_manager.emit_progress(
                context.engagement_id, context.run_id, self.name,
                completed, len(targets), f"Hunting: {completed}/{len(targets)} targets complete",
            )

        coverage_ratio = succeeded / len(targets) if targets else 0
        is_degraded = coverage_ratio < 0.5

        if is_degraded:
            await event_manager.emit_error(
                context.engagement_id, context.run_id, self.name,
                f"Degraded coverage: only {succeeded}/{len(targets)} target agents completed successfully ({coverage_ratio:.0%})",
            )

        self.write_output(context, "all_findings.json", all_findings)

        for finding in all_findings:
            create_bug(context.engagement_id, context.run_id, finding)

        return StageResult(
            success=True,
            input_count=len(targets),
            output_count=len(all_findings),
            cost_usd=total_cost,
            metadata={
                "targets_succeeded": succeeded,
                "targets_failed": failed,
                "coverage_ratio": round(coverage_ratio, 2),
                "degraded": is_degraded,
            },
        )

    async def _map_codebase(self, source_path: str, config) -> dict:
        """Map the codebase directory structure."""
        codebase_map = {"root": source_path, "modules": [], "total_files": 0, "total_lines": 0}
        exclude = set(config.exclude_paths or [
            "node_modules", ".git", "__pycache__", "vendor", ".venv", "venv",
            "dist", "build", ".next", ".nuxt", "target",
        ])
        extensions = set(config.file_extensions) if config.file_extensions else None

        for root, dirs, files in os.walk(source_path):
            dirs[:] = [d for d in dirs if d not in exclude]

            rel_root = os.path.relpath(root, source_path)
            module_files = []

            for f in files:
                if extensions and not any(f.endswith(ext) for ext in extensions):
                    continue
                filepath = os.path.join(root, f)
                try:
                    line_count = sum(1 for _ in open(filepath, "r", errors="ignore"))
                    size = os.path.getsize(filepath)
                    module_files.append({
                        "path": os.path.relpath(filepath, source_path),
                        "lines": line_count,
                        "size": size,
                    })
                    codebase_map["total_files"] += 1
                    codebase_map["total_lines"] += line_count
                except (OSError, UnicodeDecodeError):
                    continue

            if module_files:
                total_lines = sum(f["lines"] for f in module_files)
                codebase_map["modules"].append({
                    "path": rel_root,
                    "files": module_files,
                    "total_lines": total_lines,
                    "file_count": len(module_files),
                })

        return codebase_map

    def _split_into_chunks(self, codebase_map: dict, context_budget: int) -> list[dict]:
        """Split codebase modules into context-sized chunks."""
        tokens_per_line = 1.3
        max_lines_per_chunk = int(context_budget / tokens_per_line * 0.6)

        modules = sorted(codebase_map["modules"], key=lambda m: m["total_lines"], reverse=True)
        chunks = []
        current_chunk = {"modules": [], "total_lines": 0, "files": []}

        for module in modules:
            if module["total_lines"] > max_lines_per_chunk:
                file_batch = {"modules": [module["path"]], "total_lines": 0, "files": []}
                for f in module["files"]:
                    if file_batch["total_lines"] + f["lines"] > max_lines_per_chunk:
                        if file_batch["files"]:
                            chunks.append(file_batch)
                        file_batch = {"modules": [module["path"]], "total_lines": 0, "files": []}
                    file_batch["files"].append(f["path"])
                    file_batch["total_lines"] += f["lines"]
                if file_batch["files"]:
                    chunks.append(file_batch)
            elif current_chunk["total_lines"] + module["total_lines"] > max_lines_per_chunk:
                if current_chunk["files"]:
                    chunks.append(current_chunk)
                current_chunk = {
                    "modules": [module["path"]],
                    "total_lines": module["total_lines"],
                    "files": [f["path"] for f in module["files"]],
                }
            else:
                current_chunk["modules"].append(module["path"])
                current_chunk["total_lines"] += module["total_lines"]
                current_chunk["files"].extend(f["path"] for f in module["files"])

        if current_chunk["files"]:
            chunks.append(current_chunk)

        return chunks if chunks else [{"modules": ["."], "total_lines": 0,
                                       "files": [f["path"] for m in codebase_map["modules"] for f in m["files"]]}]

    async def _run_subagent(self, context: StageContext, chunk: dict,
                            agent_name: str, source_path: str) -> CLIResult:
        """Run a single bug hunter subagent on a code chunk."""
        files_list = "\n".join(chunk["files"][:100])
        if len(chunk["files"]) > 100:
            files_list += f"\n... and {len(chunk['files']) - 100} more files"

        prompt = f"""You are auditing the following source code files for security vulnerabilities.

SOURCE CODE ROOT: {source_path}

FILES TO AUDIT:
{files_list}

MODULES: {', '.join(chunk['modules'])}

INSTRUCTIONS:
1. Read and analyze each file thoroughly for security vulnerabilities
2. Flag ALL potential vulnerabilities — do not filter or prioritize, maximize coverage
3. For each finding, provide: file path, line range, vulnerability class (CWE), type, description, reasoning, and confidence level
4. Also produce a security-focused functionality summary of the code you reviewed

Output your response as JSON matching the required schema."""

        agent_file = str(AGENTS_DIR / "source_code" / "bug_hunter_subagent.md")

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
                    loop.create_task(
                        event_manager.emit_agent_stream(
                            context.engagement_id, context.run_id, self.name,
                            agent_name, text[:500],
                        )
                    )
                except RuntimeError:
                    pass

        schema_file = str(Path(__file__).parent.parent.parent.parent / "schemas" / "bug_hunter_output.json")

        if agent_name == "claude" or agent_name.startswith("claude"):
            return await run_claude(
                prompt=prompt,
                agent_file=agent_file,
                model=context.config.models.bug_hunter_subagent,
                cwd=source_path,
                json_schema_file=schema_file,
                timeout=context.config.pipeline.subagent_timeout,
                on_event=on_event,
            )
        elif agent_name == "codex" or agent_name.startswith("codex"):
            return await run_codex(
                prompt=prompt,
                model=context.config.broad_bug_hunter.codex_model,
                cwd=source_path,
                timeout=context.config.pipeline.subagent_timeout,
                on_event=on_event,
            )
        else:
            return CLIResult(success=False, error=f"Unknown agent: {agent_name}")

    async def _run_phase2(self, context: StageContext, summaries: list[dict],
                          source_path: str, stage_dir: str) -> list[dict]:
        """Phase 2: identify cross-component interactions and deploy targeted subagents."""
        phase2_dir = os.path.join(stage_dir, "phase2")
        os.makedirs(phase2_dir, exist_ok=True)

        summaries_text = json.dumps(summaries, indent=2)[:50000]

        orchestrator_prompt = f"""You are analyzing functionality summaries from different parts of a codebase to identify
potential cross-component security vulnerabilities (logic bugs, trust boundary violations,
auth bypasses that span modules, etc.).

FUNCTIONALITY SUMMARIES:
{summaries_text}

For each suspicious cross-component interaction you identify, output a JSON array of objects, each with:
- "hypothesis": description of the potential vulnerability
- "modules_involved": list of module paths to examine
- "files_to_examine": specific files that should be analyzed together
- "reasoning": why this interaction might be vulnerable

Focus on:
- Trust boundary violations (one module trusts input that another module allows users to control)
- Authentication/authorization gaps between modules
- Data flow paths where sanitization is assumed but not enforced
- Race conditions in cross-module state management"""

        result = await run_claude(
            prompt=orchestrator_prompt,
            model=context.config.models.bug_hunter_orchestrator,
            cwd=source_path,
            timeout=context.config.pipeline.subagent_timeout,
        )

        if not result.success or not result.result:
            return []

        interactions = result.result if isinstance(result.result, list) else []
        if isinstance(result.result, dict):
            interactions = result.result.get("interactions", [])

        phase2_findings = []
        for i, interaction in enumerate(interactions[:10]):
            files = interaction.get("files_to_examine", [])
            hypothesis = interaction.get("hypothesis", "")

            subagent_prompt = f"""Investigate this potential cross-component vulnerability:

HYPOTHESIS: {hypothesis}
REASONING: {interaction.get('reasoning', '')}
FILES TO EXAMINE: {json.dumps(files)}
SOURCE ROOT: {source_path}

Read the specified files and determine if this cross-component interaction creates a real
security vulnerability. If it does, output findings in the standard bug finding format."""

            agent_file = str(AGENTS_DIR / "source_code" / "bug_hunter_logic_subagent.md")
            sub_result = await run_claude(
                prompt=subagent_prompt,
                agent_file=agent_file,
                model=context.config.models.bug_hunter_subagent,
                cwd=source_path,
                timeout=context.config.pipeline.subagent_timeout,
            )

            if sub_result.success and sub_result.result:
                findings = sub_result.result if isinstance(sub_result.result, list) else sub_result.result.get("findings", [])
                run_prefix = context.run_id[:8]
                for j, finding in enumerate(findings):
                    if "id" not in finding:
                        finding["id"] = f"{run_prefix}/logic-{i:03d}-{j:03d}"
                    finding["found_by"] = ["claude-phase2"]
                phase2_findings.extend(findings)

            with open(os.path.join(phase2_dir, f"interaction_{i:03d}.json"), "w") as f:
                json.dump({
                    "hypothesis": interaction,
                    "findings": findings if sub_result.success else [],
                    "error": sub_result.error if not sub_result.success else "",
                }, f, indent=2)

        return phase2_findings

    async def _run_black_box_hunter(self, context: StageContext, target: dict,
                                    infra_config: str, target_dir: str) -> CLIResult:
        """Run a black box bug hunter on a single target."""
        target_info = json.dumps(target, indent=2)

        prompt = f"""You are performing a black-box security assessment of the following target.

TARGET INFORMATION:
{target_info}

INFRASTRUCTURE ACCESS:
{infra_config}

INSTRUCTIONS:
1. Understand the target's functionality, endpoints, and attack surface
2. Use curl, python requests, or other tools to interact with the target
3. Test for all vulnerability classes: injection, authentication bypass, IDOR, SSRF, XSS, CSRF, etc.
4. Test all user roles if credentials are provided
5. For each bug found, include the HTTP request/response evidence
6. If you hit an authentication barrier you cannot bypass programmatically (MFA, CAPTCHA),
   note it and move on
7. Write your progress to {target_dir}/progress.json after each meaningful test

Output findings as a JSON object with a "findings" array."""

        agent_file = str(AGENTS_DIR / "black_box" / "bug_hunter.md")

        return await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.black_box_bug_hunter,
            timeout=context.config.pipeline.subagent_timeout,
        )
