"""Summarizer stage — generate a comprehensive markdown report of all findings.

Consumes everything: confirmed bugs, cannot-validate bugs, out-of-scope bugs,
discarded bugs, informational findings, chains, and program/scope details.
Produces a ranked markdown report recommending which bugs to submit.
"""

from __future__ import annotations

import json
import logging
import os

from bug_hunter.core.cli_wrapper import run_claude
from bug_hunter.core.database import list_bugs, list_chains
from bug_hunter.core.events import event_manager
from bug_hunter.utils.result_parser import parse_agent_result
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)


@register
class SummarizerStage(PipelineStage):

    @property
    def name(self) -> str:
        return "summarizer"

    async def execute(self, context: StageContext) -> StageResult:
        stage_dir = self.get_stage_dir(context)
        eng_config = context.engagement["config"]

        # Gather ALL bugs across all statuses
        all_bugs = list_bugs(context.engagement_id)
        confirmed = [b for b in all_bugs if b["status"] == "confirmed"]
        cannot_validate = [b for b in all_bugs if b["status"] == "cannot_validate"]
        out_of_scope = [b for b in all_bugs if b["status"] == "out_of_scope"]
        informational = [b for b in all_bugs if b["status"] == "informational"]
        discarded = [b for b in all_bugs if b["status"] == "discarded"]
        triage_failed = [b for b in all_bugs if b["status"] == "triage_failed"]

        # Gather chains
        chains = list_chains(context.engagement_id)

        # Load scope/architecture from scoper
        scope_data = self.read_previous_output(context, "scoper", "scope.json")
        if not scope_data:
            # Try from a prior run
            for prev_stage_name in ["scoper"]:
                scope_data = self.read_previous_output(context, prev_stage_name, "scope.json")
                if scope_data:
                    break
        scope_data = scope_data or {}

        # Write all data to files so LLM reads on its own
        scope_def = eng_config.get("engagement", {}).get("scope_definition", "")
        infra_config = eng_config.get("engagement", {}).get("infra_config", "")

        stage_dir = self.get_stage_dir(context)
        scope_file = self._stage_output_path(context, "scoper", "scope.json")

        def _write_bugs(filename, bugs):
            path = os.path.join(stage_dir, filename)
            with open(path, "w") as f:
                json.dump([b["bug_data"] for b in bugs], f, indent=2, default=str)
            return os.path.abspath(path)

        confirmed_file = _write_bugs("input_confirmed.json", confirmed)
        cannot_validate_file = _write_bugs("input_cannot_validate.json", cannot_validate)
        out_of_scope_file = _write_bugs("input_out_of_scope.json", out_of_scope)
        informational_file = _write_bugs("input_informational.json", informational)
        discarded_file = _write_bugs("input_discarded.json", discarded)
        triage_failed_file = _write_bugs("input_triage_failed.json", triage_failed)

        chains_path = os.path.join(stage_dir, "input_chains.json")
        with open(chains_path, "w") as f:
            json.dump([c["chain_data"] for c in chains], f, indent=2, default=str)
        chains_file = os.path.abspath(chains_path)

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Generating summary report: {len(confirmed)} confirmed, {len(cannot_validate)} cannot-validate, "
            f"{len(out_of_scope)} out-of-scope, {len(informational)} informational, {len(discarded)} discarded",
        )

        prompt = f"""You are a senior security consultant writing a comprehensive bug bounty engagement report.

PROGRAM / SCOPE OVERVIEW:
{scope_def[:5000]}

INFRASTRUCTURE:
{infra_config[:2000]}

APPLICATION ARCHITECTURE: Read {scope_file} for architecture details.

Read these files for the full bug data:
- CONFIRMED BUGS ({len(confirmed)} total): {confirmed_file}
- CANNOT VALIDATE ({len(cannot_validate)} total): {cannot_validate_file}
- OUT OF SCOPE ({len(out_of_scope)} total): {out_of_scope_file}
- INFORMATIONAL ({len(informational)} total): {informational_file}
- DISCARDED ({len(discarded)} total): {discarded_file}
- TRIAGE FAILED ({len(triage_failed)} total): {triage_failed_file}
- BUG CHAINS ({len(chains)} total): {chains_file}

Write a comprehensive markdown report with these sections:

# Engagement Summary Report

## Program Overview
Brief description of the target, scope, and methodology.

## Executive Summary
High-level findings overview — how many bugs found, key themes, overall risk assessment.

## Recommended Submissions (ranked)
For each bug you recommend submitting, ranked by likelihood of acceptance × security impact:

### 1. [Bug Title] — [Severity]
- **Likelihood of acceptance**: High/Medium/Low with reasoning
- **Root Cause**: What's wrong and where (file:line if available)
- **Security Impact**: What an attacker can achieve
- **PoC**: The proof-of-concept code or steps
- **Expanded Impact**: Any demonstrated escalations from the Perfectionist

(Repeat for each recommended bug)

## Bug Chains
Describe any chains that combine multiple bugs for higher impact.

## Cannot Validate — Worth Revisiting
Bugs that couldn't be proven but look promising for manual follow-up.

## Out of Scope Findings
Bugs found but excluded by scope rules — note for future engagements.

## Informational Findings
Intelligence gathered (internal IPs, versions, architecture details).

## Statistics
- Total bugs found: X
- Confirmed: X (by severity breakdown)
- Cannot validate: X
- Out of scope: X
- Informational: X
- Discarded: X
- Chains: X demonstrated, X proposed

CRITICAL: Output ONLY the raw markdown text. Do NOT wrap it in JSON or code blocks. Start directly with `# Engagement Summary Report`."""

        record_dir, record_meta = self.prepare_agent_run(
            context, "claude", "summarizer",
            {"model": context.config.models.bug_chainer, "total_bugs": len(all_bugs)},
        )

        result = await run_claude(
            prompt=prompt,
            model=context.config.models.bug_chainer,  # Use same model as chainer
            timeout=context.config.pipeline.subagent_timeout,
            record_dir=record_dir,
            record_metadata=record_meta,
        )

        if not result.success:
            # Generate a basic report from data
            report = self._generate_fallback_report(
                confirmed, cannot_validate, out_of_scope, informational, discarded, triage_failed, chains, scope_def,
            )
        else:
            # The summarizer expects markdown, not JSON. The CLI wrapper's
            # _parse_result_payload may have eagerly parsed an embedded JSON
            # snippet from the markdown (e.g., a PoC payload example).
            # Recover the original text from raw_output if result is not markdown.
            report = result.result if isinstance(result.result, str) else ""

            # If result was parsed as JSON (dict/list), the CLI wrapper eagerly
            # extracted an embedded JSON snippet from the markdown. Recover the
            # full report from the stream.jsonl file.
            if not report or not report.strip().startswith('#'):
                recovered = self._recover_text_from_stream(record_dir)
                if recovered and '# ' in recovered:
                    import re
                    md_match = re.search(r'# Engagement Summary Report[\s\S]*', recovered)
                    report = md_match.group(0) if md_match else recovered
                elif isinstance(result.result, dict):
                    report = self._generate_fallback_report(
                        confirmed, cannot_validate, out_of_scope, informational,
                        discarded, triage_failed, chains, scope_def,
                    )

            # Clean up code block wrappers if present
            if report.startswith('```'):
                import re
                report = re.sub(r'^```(?:markdown)?\n?', '', report)
                report = re.sub(r'\n?```\s*$', '', report)

        # Save the report
        report_path = os.path.join(stage_dir, "report.md")
        with open(report_path, "w") as f:
            f.write(report)

        # Also save to cumulative directory
        cumulative_path = os.path.join(context.cumulative_dir, "report.md")
        with open(cumulative_path, "w") as f:
            f.write(report)

        self.write_output(context, "report_metadata.json", {
            "confirmed": len(confirmed),
            "cannot_validate": len(cannot_validate),
            "out_of_scope": len(out_of_scope),
            "informational": len(informational),
            "discarded": len(discarded),
            "triage_failed": len(triage_failed),
            "chains": len(chains),
            "report_length": len(report),
        })

        return StageResult(
            success=True,
            input_count=len(all_bugs),
            output_count=1,
            cost_usd=result.cost_usd if result.success else 0,
            metadata={"report_length": len(report), "total_bugs": len(all_bugs)},
        )

    def _recover_text_from_stream(self, record_dir: str) -> str:
        """Extract all assistant text blocks from a stream.jsonl file."""
        import json as _json
        stream_path = os.path.join(record_dir, "stream.jsonl")
        if not os.path.exists(stream_path):
            return ""
        texts = []
        try:
            with open(stream_path) as f:
                for line in f:
                    try:
                        entry = _json.loads(line.strip())
                        raw = _json.loads(entry.get("raw", "{}"))
                        if raw.get("type") == "assistant":
                            content = raw.get("message", {}).get("content", [])
                            if isinstance(content, list):
                                for block in content:
                                    if block.get("type") == "text":
                                        texts.append(block["text"])
                    except (_json.JSONDecodeError, KeyError, TypeError):
                        continue
        except Exception:
            return ""
        return "".join(texts)

    def _generate_fallback_report(self, confirmed, cannot_validate, out_of_scope,
                                   informational, discarded, triage_failed, chains, scope_def):
        """Generate a basic report when the LLM fails."""
        lines = ["# Engagement Summary Report\n"]
        lines.append("## Executive Summary\n")
        lines.append(f"- **Confirmed bugs**: {len(confirmed)}")
        lines.append(f"- **Cannot validate**: {len(cannot_validate)}")
        lines.append(f"- **Out of scope**: {len(out_of_scope)}")
        lines.append(f"- **Informational**: {len(informational)}")
        lines.append(f"- **Discarded**: {len(discarded)}")
        lines.append(f"- **Chains**: {len(chains)}\n")

        if confirmed:
            lines.append("## Confirmed Bugs\n")
            for b in confirmed:
                d = b["bug_data"]
                lines.append(f"### {d.get('id', '?')} — {d.get('vuln_type', '?')} [{d.get('severity', '?')}]")
                if d.get("description"):
                    lines.append(f"\n{d['description']}\n")
                if d.get("root_cause"):
                    lines.append(f"**Root Cause**: {d['root_cause']}\n")
                if d.get("security_impact"):
                    lines.append(f"**Impact**: {d['security_impact']}\n")

        return "\n".join(lines)
