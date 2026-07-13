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
import shutil
from pathlib import Path
from typing import Any, Optional

from uuid import uuid4

from bug_hunter.core.cli_wrapper import CLIResult, StreamEvent, run_claude, run_codex
from bug_hunter.core.sandbox import SRC, WORK, ContainerSpec
from bug_hunter.core.database import create_bug
from bug_hunter.core.events import event_manager
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register
from bug_hunter.utils.schema_validator import validate_findings_list

logger = logging.getLogger(__name__)
AGENTS_DIR = Path(__file__).parent.parent.parent.parent / "agents"
SCHEMAS_DIR = Path(__file__).parent.parent.parent.parent / "schemas"


@register
class BugHunterStage(PipelineStage):

    def __init__(self):
        pass

    @property
    def name(self) -> str:
        return "bug_hunter"

    def _get_sessions_file(self, context: StageContext) -> str:
        """Get the path to the engagement-level sessions file for persistent agent sessions."""
        eng_dir = os.path.dirname(os.path.dirname(context.run_dir))  # engagement dir
        return os.path.join(eng_dir, "agent_sessions.json")

    def _load_sessions(self, context: StageContext) -> dict:
        path = self._get_sessions_file(context)
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}

    def _save_sessions(self, context: StageContext, sessions: dict):
        path = self._get_sessions_file(context)
        with open(path, "w") as f:
            json.dump(sessions, f, indent=2)

    def _get_agent_session(self, context: StageContext, agent_name: str) -> tuple[str, bool]:
        """Get or create a persistent session ID for an agent. Returns (session_id, is_resume).

        Only returns is_resume=True if the session was previously used successfully
        (marked by a _used suffix key). Otherwise generates a fresh session ID
        every time to avoid "session ID already in use" errors from failed attempts.
        """
        sessions = self._load_sessions(context)
        key = f"bug_hunter_{agent_name}"
        used_key = f"{key}_used"
        if key in sessions and sessions.get(used_key):
            return sessions[key], True
        # Always generate fresh ID for new/unused sessions
        new_id = str(uuid4())
        sessions[key] = new_id
        sessions.pop(used_key, None)
        self._save_sessions(context, sessions)
        return new_id, False

    def _mark_session_used(self, context: StageContext, agent_name: str):
        """Mark a session as successfully used so future calls resume it."""
        sessions = self._load_sessions(context)
        key = f"bug_hunter_{agent_name}_used"
        sessions[key] = True
        self._save_sessions(context, sessions)

    async def execute(self, context: StageContext) -> StageResult:
        stage_dir = self.get_stage_dir(context)
        eng_config = context.engagement["config"]
        eng_type = context.engagement["type"]
        hunter_config = context.config.bug_hunter
        infra_config = eng_config.get("engagement", {}).get("infra_config", "")

        # Write program details to a file the agent can read
        eng_dir = os.path.dirname(os.path.dirname(context.run_dir))
        program_file = os.path.join(eng_dir, "program.json")
        if not os.path.exists(program_file):
            eng_details = eng_config.get("engagement", {})
            program_data = {
                "name": context.engagement.get("name", ""),
                "type": eng_type,
                "scope_definition": eng_details.get("scope_definition", ""),
                "infra_config": infra_config,
                "target_domains": eng_details.get("target_domains", []),
                "source_repo": eng_details.get("source_repo", ""),
            }
            # Include raw platform data if available (from YWH import)
            raw_program = eng_config.get("raw_program_data")
            if raw_program:
                program_data["raw_program_data"] = raw_program
            with open(program_file, "w") as f:
                json.dump(program_data, f, indent=2)

        # Get source path and available tools from setup
        setup_data = self.read_previous_output(context, "setup", "setup.json")
        source_path = ""
        if setup_data and "source" in setup_data:
            source_path = setup_data["source"]["local_path"]
        if not source_path:
            source_path = eng_config.get("engagement", {}).get("source_path", "")

        available_tools = ""
        if setup_data and "tools" in setup_data:
            tool_list = [
                f"{t['name']} ({t['path']})" for t in setup_data["tools"]
                if t.get("available") and t["name"] not in ("claude", "codex", "git", "python3", "pip3", "curl")
            ]
            if tool_list:
                available_tools = "AVAILABLE TOOLS: " + ", ".join(tool_list)

        bugs_file = os.path.join(stage_dir, "BUGS.json")
        if not os.path.exists(bugs_file):
            with open(bugs_file, "w") as f:
                json.dump([], f, indent=2)

        # Engagement-level tracking files
        eng_dir = os.path.dirname(os.path.dirname(context.run_dir))
        attack_surfaces_file = os.path.join(eng_dir, "ATTACK_SURFACES.md")
        if not os.path.exists(attack_surfaces_file):
            with open(attack_surfaces_file, "w") as f:
                f.write("")

        iterations = max(1, hunter_config.iterations)
        agents = hunter_config.agents
        mode = hunter_config.mode  # "parallel" or "sequential"
        total_cost = 0.0
        total_usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        all_new_bugs = []
        agent_stats = {agent: {"succeeded": 0, "failed": 0, "running": 0, "total": iterations} for agent in agents}

        # Notes live at the engagement level so they persist across runs
        eng_dir = os.path.dirname(os.path.dirname(context.run_dir))
        if mode == "sequential":
            notes_files = {agent: os.path.join(eng_dir, "NOTES.md") for agent in agents}
        else:
            notes_files = {agent: os.path.join(eng_dir, f"NOTES_{agent}.md") for agent in agents}
        for nf in set(notes_files.values()):
            if not os.path.exists(nf):
                with open(nf, "w") as f:
                    f.write("")

        for iteration in range(1, iterations + 1):
            with open(bugs_file) as f:
                existing_bugs = json.load(f)
            existing_bugs = [b for b in existing_bugs if isinstance(b, dict) and b.get("found_by")]

            await event_manager.emit_log(
                context.engagement_id, context.run_id, self.name,
                f"Iteration {iteration}/{iterations} ({mode}) — {len(existing_bugs)} bugs found so far",
            )

            async def _run_agent_iteration(agent_name, _existing_bugs):
                nonlocal total_cost
                agent_stats[agent_name]["running"] += 1
                await event_manager.emit("agent_progress", context.engagement_id, context.run_id, self.name, {
                    "agent": agent_name, "status": "running", "total_chunks": iterations,
                    "succeeded": agent_stats[agent_name]["succeeded"],
                    "failed": agent_stats[agent_name]["failed"],
                    "running": agent_stats[agent_name]["running"],
                })

                # Make BUGS.json read-only so agents can't overwrite it
                import stat
                for protected in [bugs_file]:
                    if os.path.exists(protected):
                        os.chmod(protected, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

                try:
                    result = await self._run_hunter(
                        context, agent_name,
                        _existing_bugs, source_path, infra_config, eng_type, stage_dir,
                        notes_files[agent_name], available_tools, attack_surfaces_file,
                        program_file,
                    )
                finally:
                    # Restore write permissions
                    for protected in [bugs_file]:
                        if os.path.exists(protected):
                            os.chmod(protected, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
                total_cost += result.cost_usd
                if result.usage:
                    for k in total_usage:
                        total_usage[k] += result.usage.get(k, 0)
                agent_stats[agent_name]["running"] -= 1

                if result.success and result.result:
                    from bug_hunter.utils.result_parser import parse_agent_result
                    raw_file = os.path.join(stage_dir, f"raw_output_{agent_name}_iter{iteration}.md")
                    data = parse_agent_result(
                        result.result, ["bugs"],
                        f"bug_hunter/{agent_name}", save_raw_to=raw_file,
                    )
                    if not data.get("bugs") and isinstance(result.result, str):
                        await event_manager.emit_log(
                            context.engagement_id, context.run_id, self.name,
                            f"[{agent_name}] iter {iteration}: text instead of JSON — saved to raw_output",
                        )
                    new_bugs = data.get("bugs", [])

                    run_prefix = context.run_id[:8]
                    for i, bug in enumerate(new_bugs):
                        bug["id"] = f"{run_prefix}/{agent_name}-i{iteration}-{len(_existing_bugs) + i:03d}"
                        bug["found_by"] = [agent_name]

                    agent_stats[agent_name]["succeeded"] += 1
                    await event_manager.emit("agent_progress", context.engagement_id, context.run_id, self.name, {
                        "agent": agent_name, "status": "iteration_done",
                        "bugs_found": len(new_bugs), "total_chunks": iterations,
                        "succeeded": agent_stats[agent_name]["succeeded"],
                        "failed": agent_stats[agent_name]["failed"],
                        "running": agent_stats[agent_name]["running"],
                    })
                    return new_bugs
                else:
                    agent_stats[agent_name]["failed"] += 1
                    await event_manager.emit("agent_progress", context.engagement_id, context.run_id, self.name, {
                        "agent": agent_name, "status": "iteration_failed",
                        "error": result.error[:200] if result.error else "", "total_chunks": iterations,
                        "succeeded": agent_stats[agent_name]["succeeded"],
                        "failed": agent_stats[agent_name]["failed"],
                        "running": agent_stats[agent_name]["running"],
                    })
                    logger.warning(f"Bug hunter ({agent_name}) iter {iteration} failed: {result.error}")
                    return []

            iteration_bugs = []
            if mode == "sequential":
                # Run agents one at a time in order; each sees the previous agent's notes and bugs
                for agent in agents:
                    # Reload bugs mid-iteration so later agents see earlier agent's findings
                    with open(bugs_file) as f:
                        current_bugs = json.load(f)
                    new_bugs = await _run_agent_iteration(agent, current_bugs)
                    iteration_bugs.extend(new_bugs)
                    # Persist immediately so next agent sees them
                    if new_bugs:
                        current_bugs.extend(new_bugs)
                        with open(bugs_file, "w") as f:
                            json.dump(current_bugs, f, indent=2)
            else:
                # Parallel: run all agents concurrently
                tasks = [_run_agent_iteration(agent, existing_bugs) for agent in agents]
                for coro in asyncio.as_completed(tasks):
                    new_bugs = await coro
                    iteration_bugs.extend(new_bugs)

            all_new_bugs.extend(iteration_bugs)

            # Update BUGS.json so next iteration sees cumulative bugs
            # (sequential mode already persists mid-iteration, so skip to avoid duplicates)
            if mode != "sequential" and iteration_bugs:
                with open(bugs_file) as f:
                    current_bugs = json.load(f)
                current_bugs.extend(iteration_bugs)
                with open(bugs_file, "w") as f:
                    json.dump(current_bugs, f, indent=2)

            await event_manager.emit_progress(
                context.engagement_id, context.run_id, self.name,
                iteration, iterations,
                f"Iteration {iteration}/{iterations} complete — {len(iteration_bugs)} new bugs",
            )

        # After all iterations — read final state
        with open(bugs_file) as f:
            combined_bugs = json.load(f)

        # Validate and persist to DB
        quarantine_dir = os.path.join(stage_dir, "quarantined")
        valid_bugs, quarantined = validate_findings_list(all_new_bugs, quarantine_dir)

        self.write_output(context, "all_findings.json", valid_bugs)

        for bug in valid_bugs:
            create_bug(context.engagement_id, context.run_id, bug)

        succeeded = sum(1 for s in agent_stats.values() if s["succeeded"])
        failed = sum(1 for s in agent_stats.values() if s["failed"])

        return StageResult(
            success=True,
            input_count=0,
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

    def _maybe_container(self, context: StageContext, agent_name: str, eng_type: str,
                         source_path: str, program_file: str, stage_dir: str) -> Optional[ContainerSpec]:
        """Build a per-agent ContainerSpec when sandbox isolation is enabled.

        Each claude/codex agent gets a private working dir (its OWN NOTES +
        ATTACK_SURFACES, a copy of program.json) mounted at /work, the source
        read-only at /src, and a persistent per-agent home (for resume). It
        cannot see the shared engagement dir or any sibling agent's work.
        """
        sb = context.config.sandbox
        if not sb.enabled or not (agent_name.startswith("claude") or agent_name.startswith("codex")):
            return None

        agent_ws = os.path.abspath(os.path.join(stage_dir, "agent_ws", agent_name))
        os.makedirs(agent_ws, exist_ok=True)
        for fname in ("NOTES.md", "ATTACK_SURFACES.md"):
            fp = os.path.join(agent_ws, fname)
            if not os.path.exists(fp):
                open(fp, "w").close()
        priv_program = os.path.join(agent_ws, "program.json")
        if program_file and os.path.exists(program_file) and not os.path.exists(priv_program):
            shutil.copy2(program_file, priv_program)

        # Source mounted read-only at /src; empty placeholder for black_box.
        if eng_type == "source_code" and source_path and os.path.isdir(source_path):
            source_host = os.path.abspath(source_path)
        else:
            source_host = os.path.join(agent_ws, "_src")
            os.makedirs(source_host, exist_ok=True)

        # Persistent per-agent home (Codex rollouts / Claude sessions) so resume
        # survives across runs. Lives at the engagement level.
        eng_dir = os.path.dirname(os.path.dirname(context.run_dir))
        agent_home = os.path.join(eng_dir, "agent_homes", agent_name)
        kind = "codex" if agent_name.startswith("codex") else "claude"
        return ContainerSpec(
            image=sb.image, kind=kind, work_host=agent_ws,
            source_host=source_host, agent_home_host=agent_home, network=sb.network,
        )

    async def _run_hunter(self, context: StageContext, agent_name: str,
                          existing_bugs: list, source_path: str,
                          infra_config: str, eng_type: str, stage_dir: str,
                          notes_file: str = "", available_tools: str = "",
                          attack_surfaces_file: str = "",
                          program_file: str = "") -> CLIResult:
        """Run a single bug hunter agent."""
        bugs_file = os.path.abspath(os.path.join(stage_dir, "BUGS.json"))
        surfaces_path = os.path.abspath(attack_surfaces_file) if attack_surfaces_file else ""
        notes_path = os.path.abspath(notes_file) if notes_file else ""

        # Determine session state early — needed for prompt construction.
        # Persistent sessions are parallel-only: each agent keeps its own
        # conversation (Claude session / Codex thread) so re-hunts resume with
        # full prior context instead of restarting from the notes files.
        session_id = None
        is_resume = False
        if context.config.bug_hunter.mode == "parallel" and (
            agent_name.startswith("claude") or agent_name.startswith("codex")
        ):
            session_id, is_resume = self._get_agent_session(context, agent_name)

        # Container isolation: when enabled, each parallel agent runs in its own
        # Docker container with a private working dir. It gets its OWN NOTES /
        # ATTACK_SURFACES (not the shared engagement files) and cannot see any
        # other agent's work — so paths handed to it are the in-container mounts.
        container = self._maybe_container(context, agent_name, eng_type, source_path,
                                          program_file, stage_dir)
        if container is not None:
            disp_program = f"{WORK}/program.json"
            disp_source = SRC
            disp_surfaces = f"{WORK}/ATTACK_SURFACES.md"
            disp_notes = f"{WORK}/NOTES.md"
            bugs_note = ("Your output will be collected automatically via structured output — "
                         "do not write findings to any file.")
        else:
            disp_program = os.path.abspath(program_file)
            disp_source = source_path
            disp_surfaces = surfaces_path
            disp_notes = notes_path
            bugs_note = ("BUGS.json is READ-ONLY. Your output will be collected automatically via "
                         "structured output — do not write findings to any file.")

        if context.run_type == "rehunt" and context.rehunt_target and is_resume:
            # Rehunt with persistent session — agent already has full context
            prompt = context.rehunt_target + "\n\nOnly include NEW bugs discovered in this session — do not re-report previously found bugs."
        else:
            rehunt_instruction = ""
            if context.rehunt_target:
                rehunt_instruction = f"""SPECIFIC INSTRUCTIONS FOR THIS RUN:
{context.rehunt_target}

The above instructions are your PRIMARY OBJECTIVE for this run. Prioritize them over general scanning.
"""
            prompt = f"""{rehunt_instruction}
You may find the full details on this engagement here: {disp_program}.

{"SOURCE CODE ROOT: " + disp_source if eng_type == "source_code" else ""}
ATTACK SURFACES: {disp_surfaces}
NOTES: {disp_notes}

{"Identify attack surfaces within the source code, find vulnerabilities throughout the codebases and document attack surfaces as you go along." if eng_type == "source_code" else "Enumerate the targets within scope and find vulnerabilities. As you go along and you identify more attack surfaces, you may update the list of target surfaces."}

{bugs_note} Only include NEW bugs discovered in this session — do not re-report previously found bugs.
When you are done, make sure all background tasks and subagents have completed before finishing."""

        if eng_type == "source_code":
            agent_file = str(AGENTS_DIR / "source_code" / "bug_hunter.md")
        else:
            agent_file = str(AGENTS_DIR / "black_box" / "bug_hunter.md")

        def on_event(event: StreamEvent):
            data = event.data or {}
            stream_data = {"agent_id": agent_name}

            if event.type == "assistant":
                content = data.get("message", {}).get("content", data.get("content", ""))
                if isinstance(content, list):
                    text = " ".join(
                        block.get("text", "") for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                elif isinstance(content, str):
                    text = content
                else:
                    text = ""
                if text:
                    stream_data["text"] = text[:500]
                    stream_data["event_type"] = "text"
                else:
                    # Check for thinking
                    delta = data.get("delta", {})
                    if delta.get("type") == "thinking_delta":
                        stream_data["thinking"] = delta.get("thinking", "")[:500]
                        stream_data["event_type"] = "thinking"
                    else:
                        return
            elif event.type == "tool_use":
                stream_data["event_type"] = "tool_use"
                stream_data["tool_name"] = data.get("name", "")
                stream_data["tool_input"] = str(data.get("input", ""))[:200]
            elif event.type == "item.completed":
                item = data.get("item", {})
                if item.get("type") == "agent_message":
                    stream_data["text"] = item.get("text", "")[:500]
                    stream_data["event_type"] = "text"
                else:
                    return
            else:
                return

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(event_manager.emit(
                    "agent_stream", context.engagement_id, context.run_id, self.name,
                    stream_data,
                ))
            except RuntimeError:
                pass

        record_dir, record_meta = self.prepare_agent_run(
            context, agent_name, "bug_hunt",
            {"model": context.config.models.bug_hunter_subagent, "engagement_type": eng_type},
        )

        # Grant access to stage_dir so agents can read/write NOTES.md
        extra_dirs = [os.path.abspath(stage_dir)]

        if eng_type == "source_code":
            cwd = source_path
        else:
            # For black_box, use engagement dir as cwd
            eng_dir = os.path.dirname(os.path.dirname(context.run_dir))
            cwd = os.path.abspath(eng_dir)

        schema_file = str(SCHEMAS_DIR / "bug_hunter.json")

        if agent_name == "claude" or agent_name.startswith("claude"):
            result = await run_claude(
                prompt=prompt,
                agent_file=agent_file if not is_resume else None,  # system prompt only on first message
                model=context.config.models.bug_hunter_subagent,
                cwd=cwd,
                json_schema_file=schema_file,
                timeout=context.config.pipeline.subagent_timeout,
                on_event=on_event,
                additional_dirs=extra_dirs,
                record_dir=record_dir,
                record_metadata=record_meta,
                session_id=session_id,
                is_resume=is_resume,
                container=container,
            )

            # If resume failed (expired/missing session), retry with a fresh session
            check_text = f"{result.error} {result.raw_output}"
            if not result.success and is_resume and "No conversation found" in check_text:
                logger.warning(f"Session expired for {agent_name}, starting fresh session")
                new_sid = str(uuid4())
                sessions = self._load_sessions(context)
                sessions[f"bug_hunter_{agent_name}"] = new_sid
                self._save_sessions(context, sessions)
                result = await run_claude(
                    prompt=prompt,
                    agent_file=agent_file,
                    model=context.config.models.bug_hunter_subagent,
                    cwd=cwd,
                    json_schema_file=schema_file,
                    timeout=context.config.pipeline.subagent_timeout,
                    on_event=on_event,
                    additional_dirs=extra_dirs,
                    record_dir=record_dir,
                    record_metadata=record_meta,
                    session_id=new_sid,
                    is_resume=False,
                    container=container,
                )
        elif agent_name == "codex" or agent_name.startswith("codex"):
            with open(agent_file) as f:
                dev_instructions = f.read()
            result = await run_codex(
                prompt=prompt,
                model=context.config.bug_hunter.codex_model,
                cwd=cwd,
                timeout=context.config.pipeline.subagent_timeout,
                on_event=on_event,
                additional_dirs=extra_dirs,
                record_dir=record_dir,
                record_metadata=record_meta,
                output_schema_file=schema_file,
                thread_id=session_id if is_resume else None,
                developer_instructions=dev_instructions,
                reasoning_effort=context.config.pipeline.codex_reasoning_effort,
                reasoning_summary=context.config.pipeline.codex_reasoning_summary,
                container=container,
            )

            # If resume failed (thread expired/missing), drop the stale thread
            # id and retry with a fresh thread.
            check_text = f"{result.error} {result.raw_output}".lower()
            if not result.success and is_resume and (
                "thread" in check_text or "not found" in check_text or "resume" in check_text
            ):
                logger.warning(f"Codex thread resume failed for {agent_name}, starting fresh thread")
                sessions = self._load_sessions(context)
                sessions.pop(f"bug_hunter_{agent_name}", None)
                sessions.pop(f"bug_hunter_{agent_name}_used", None)
                self._save_sessions(context, sessions)
                result = await run_codex(
                    prompt=prompt,
                    model=context.config.bug_hunter.codex_model,
                    cwd=cwd,
                    timeout=context.config.pipeline.subagent_timeout,
                    on_event=on_event,
                    additional_dirs=extra_dirs,
                    record_dir=record_dir,
                    record_metadata=record_meta,
                    output_schema_file=schema_file,
                    thread_id=None,
                    developer_instructions=dev_instructions,
                    reasoning_effort=context.config.pipeline.codex_reasoning_effort,
                    reasoning_summary=context.config.pipeline.codex_reasoning_summary,
                    container=container,
                )
        else:
            return CLIResult(success=False, error=f"Unknown agent: {agent_name}")

        # Save session/thread ID and mark as used for future resumption
        if result.success and result.session_id:
            sessions = self._load_sessions(context)
            sessions[f"bug_hunter_{agent_name}"] = result.session_id
            self._save_sessions(context, sessions)
            self._mark_session_used(context, agent_name)

        return result


