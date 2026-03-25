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

from bug_hunter.core.cli_wrapper import run_claude
from bug_hunter.core.database import list_bugs, update_bug
from bug_hunter.core.events import event_manager
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents")


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

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Tagging {len(bug_data_list)} findings as strong/weak/informational",
        )

        prompt = f"""You are evaluating the quality and strength of security vulnerability findings.
Tag each bug as "strong", "weak", or "informational".

FINDINGS ({len(bug_data_list)} total):
{json.dumps(bug_data_list, indent=2)[:80000]}

TAGGING CRITERIA:

**strong** — High-confidence, impactful finding:
- Clear root cause with specific code/endpoint reference
- Working PoC with successful execution result
- Meaningful security impact (data access, auth bypass, RCE, etc.)
- Well-documented exploitation path

**weak** — Real finding but needs work:
- Plausible vulnerability but PoC failed, is missing, or is incomplete
- Impact is unclear or requires unlikely preconditions
- Root cause is vague or the exploitation path is theoretical
- Valid finding that a program might accept but at lower confidence

**informational** — Not a vulnerability, but useful intelligence:
- Internal IPs, version strings, stack traces, debug info
- No direct exploitable security impact
- Useful context for other bugs (chain construction, infrastructure mapping)

For each finding, output its id, tag, severity, and a brief note explaining why.

SEVERITY LEVELS:
- **critical**: Full system compromise, RCE, unauthenticated database access, mass data breach
- **high**: Significant data exposure, auth bypass, SSRF to internal services, privilege escalation
- **medium**: Limited data exposure, CSRF with impact, stored XSS, IDOR on non-critical data
- **low**: Minor information disclosure, reflected XSS with limited impact, missing security controls

Output JSON:
{{
  "tagged": [
    {{"id": "bug-001", "tag": "strong", "severity": "high", "note": "Working SQLi with database dump PoC"}},
    {{"id": "bug-002", "tag": "weak", "severity": "medium", "note": "SSRF identified but PoC could not reach internal services"}},
    {{"id": "bug-003", "tag": "informational", "severity": "informational", "note": "Server version disclosed in headers"}}
  ]
}}"""

        agent_file = os.path.join(AGENTS_DIR, "shared", "triager.md")
        if not os.path.exists(agent_file):
            agent_file = None

        record_dir, record_meta = self.prepare_agent_run(
            context, "claude", "triage_tagging",
            {"finding_count": len(bug_data_list)},
        )

        result = await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.strict_triager,
            timeout=min(context.config.pipeline.subagent_timeout, 600),  # fast pass
            record_dir=record_dir,
            record_metadata=record_meta,
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

        triage_result = result.result if isinstance(result.result, dict) else {}
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
