"""Triager stage — lightweight bug quality tagger.

Tags each bug as "strong", "weak", or "informational" based on:
- Bug details and root cause clarity
- PoC quality and execution results
- Demonstrated security impact
- Expanded primitives (if any)

This is a fast pass, not a deep re-evaluation. Does not remove bugs —
just tags them for human review prioritization.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from bug_hunter.core.cli_wrapper import run_agent
from bug_hunter.core.database import list_bugs, update_bug
from bug_hunter.core.events import event_manager
from bug_hunter.utils.result_parser import parse_agent_result
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents")
SCHEMAS_DIR = Path(__file__).parent.parent.parent.parent / "schemas"


@register
class StrictTriagerStage(PipelineStage):

    @property
    def name(self) -> str:
        return "strict_triager"

    async def execute(self, context: StageContext) -> StageResult:
        bugs = list_bugs(context.engagement_id, status="expanded", run_id=context.run_id)
        if not bugs:
            bugs = list_bugs(context.engagement_id, status="validated", run_id=context.run_id)

        if not bugs:
            self.write_output(context, "tagged_bugs.json", [])
            return StageResult(success=True, input_count=0, output_count=0)

        bug_data_list = [b["bug_data"] for b in bugs]

        # Write findings to file so LLM reads on its own
        stage_dir = self.get_stage_dir(context)
        findings_file = os.path.join(stage_dir, "input_findings.json")
        with open(findings_file, "w") as f:
            json.dump(bug_data_list, f, indent=2)
        findings_path = os.path.abspath(findings_file)

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Tagging {len(bug_data_list)} findings as strong/weak/informational",
        )

        prompt = f"""You are evaluating security vulnerability findings. Assign each bug TWO independent ratings:
1. **Severity** (CVSS-aligned): how impactful is the vulnerability?
2. **Confidence**: how confident are we that this bug is real and exploitable?

FINDINGS ({len(bug_data_list)} total): Read {findings_path}

SEVERITY (based on security impact, follows CVSS):
- **critical** (9.0-10.0): Full system compromise, RCE, unauthenticated database wipe, mass PII breach, complete auth bypass
- **high** (7.0-8.9): Significant data exposure, privilege escalation, SSRF to internal services, stored XSS with session theft
- **medium** (4.0-6.9): Limited data exposure, CSRF with meaningful impact, IDOR on non-critical data, information disclosure enabling further attacks
- **low** (0.1-3.9): Minor information disclosure, missing security headers with no direct exploit, low-impact misconfigurations

CONFIDENCE (based on evidence quality):
- **strong**: Clear root cause with specific code/endpoint reference, working PoC with successful execution, well-documented exploitation path
- **weak**: Plausible vulnerability but PoC is missing/failed/incomplete, impact is unclear, root cause is vague or theoretical
- **informational**: Not a vulnerability — internal IPs, version strings, stack traces, debug info. Useful intelligence only.

For each finding, output its id, tag (confidence), severity, and a brief note.

Your output will be collected automatically via structured JSON output. Do not write results to any file."""

        agent_file = os.path.join(AGENTS_DIR, "shared", "triager.md")
        if not os.path.exists(agent_file):
            agent_file = None

        record_dir, record_meta = self.prepare_agent_run(
            context, self._agent_name_for_model(context.config.models.strict_triager), "triage_tagging",
            {"finding_count": len(bug_data_list)},
        )

        result = await run_agent(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.strict_triager,
            timeout=min(context.config.pipeline.subagent_timeout, 600),  # fast pass
            record_dir=record_dir,
            record_metadata=record_meta,
            json_schema_file=str(SCHEMAS_DIR / "strict_triager.json"),
        )

        if not result.success:
            # On failure, tag everything as "untagged" and continue
            for bug in bugs:
                merged = dict(bug["bug_data"])
                merged["tag"] = "untagged"
                merged["triager_notes"] = "Triager failed — untagged"
                update_bug(bug["id"], status="confirmed", bug_data=merged)
            self.write_output(context, "tagged_bugs.json", [dict(b["bug_data"], tag="untagged") for b in bugs])

            await event_manager.emit_error(
                context.engagement_id, context.run_id, self.name,
                f"Triager failed — {len(bugs)} bugs passed through as untagged",
            )
            return StageResult(
                success=True, input_count=len(bugs), output_count=len(bugs),
                cost_usd=result.cost_usd,
                metadata={"triage_failed": True},
            )

        triage_result = parse_agent_result(result.result, ['tagged'], "strict_triager")
        tagged_list = triage_result.get("tagged", [])
        tag_map = {t.get("id"): t for t in tagged_list}

        strong_count = 0
        weak_count = 0
        info_count = 0

        for bug in bugs:
            bid = bug["bug_data"].get("id")
            tag_info = tag_map.get(bid, {})
            tag = tag_info.get("tag", "untagged")
            note = tag_info.get("note", "")

            merged = dict(bug["bug_data"])
            merged["tag"] = tag
            merged["triager_notes"] = note
            merged["severity"] = tag_info.get("severity", "medium" if tag != "informational" else "informational")

            if tag == "informational":
                merged["severity"] = "informational"
                update_bug(bug["id"], status="informational", bug_data=merged)
                info_count += 1
            else:
                update_bug(bug["id"], status="confirmed", bug_data=merged)
                if tag == "strong":
                    strong_count += 1
                else:
                    weak_count += 1

        all_tagged = []
        for bug in bugs:
            bid = bug["bug_data"].get("id")
            tag_info = tag_map.get(bid, {})
            entry = dict(bug["bug_data"])
            entry["tag"] = tag_info.get("tag", "untagged")
            entry["triager_notes"] = tag_info.get("note", "")
            all_tagged.append(entry)

        self.write_output(context, "tagged_bugs.json", all_tagged)

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Tagged: {strong_count} strong, {weak_count} weak, {info_count} informational",
        )

        return StageResult(
            success=True,
            input_count=len(bugs),
            output_count=strong_count + weak_count,
            cost_usd=result.cost_usd,
            metadata={
                "strong": strong_count,
                "weak": weak_count,
                "informational": info_count,
            },
        )
