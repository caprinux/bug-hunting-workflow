"""Scope Enumeration stage — expand wildcards, enumerate subdomains, map attack surface."""

from __future__ import annotations

import json
import logging
import os

from bug_hunter.core.cli_wrapper import run_claude
from bug_hunter.core.events import event_manager
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents")


@register
class ScopeEnumeratorStage(PipelineStage):

    @property
    def name(self) -> str:
        return "scope_enumerator"

    async def execute(self, context: StageContext) -> StageResult:
        if context.engagement["type"] != "black_box":
            return StageResult(success=True, metadata={"skipped": "not black_box"})

        stage_dir = self.get_stage_dir(context)
        eng_config = context.engagement["config"]
        domains = eng_config.get("engagement", {}).get("target_domains", [])
        scope_def = eng_config.get("engagement", {}).get("scope_definition", "")
        recon_mode = context.config.scope_enumerator.recon_mode

        if not domains:
            return StageResult(success=False, error="No target domains specified")

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            f"Enumerating scope for: {', '.join(domains)}",
        )

        prompt = f"""You are performing reconnaissance on the following target domains to map the complete attack surface.

TARGET DOMAINS:
{json.dumps(domains, indent=2)}

SCOPE DEFINITION:
{scope_def}

RECON MODE: {recon_mode}

INSTRUCTIONS:
1. Use available reconnaissance tools to enumerate the target:
   - Passive: certificate transparency (crt.sh), DNS records, WHOIS, wayback machine URLs
   - Active: subdomain brute-forcing (subfinder), port scanning (nmap), HTTP probing (httpx), web crawling
   - Mode "{recon_mode}" means: {"both passive and active" if recon_mode == "both" else recon_mode + " only"}

2. For each discovered target, gather:
   - Subdomains and IP addresses
   - Open ports and services
   - Technology stack (web server, framework, CMS, WAF)
   - Discovered endpoints and parameters
   - Authentication mechanisms observed

3. Write intermediate results to files:
   - {stage_dir}/subdomains.json — list of discovered subdomains
   - {stage_dir}/ports.json — port scan results
   - {stage_dir}/tech_stack.json — technology fingerprints
   - {stage_dir}/endpoints.json — discovered endpoints

4. Produce a final attack surface map as JSON with structure:
   {{
     "targets": [
       {{
         "domain": "example.com",
         "ip": "1.2.3.4",
         "ports": [80, 443],
         "tech_stack": ["nginx", "django"],
         "endpoints": ["/api/v1/users", "/login"],
         "parameters": ["id", "username"],
         "auth_mechanism": "JWT",
         "notes": "..."
       }}
     ],
     "total_subdomains": 42,
     "total_live_targets": 15
   }}

Output the attack surface map as your final JSON response."""

        agent_file = os.path.join(AGENTS_DIR, "black_box", "scope_enumerator.md")

        result = await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.scope_enumerator,
            timeout=context.config.pipeline.subagent_timeout * 5,
        )

        if not result.success:
            return StageResult(success=False, error=result.error, cost_usd=result.cost_usd)

        attack_surface = result.result or {"targets": [], "total_subdomains": 0, "total_live_targets": 0}
        self.write_output(context, "attack_surface_map.json", attack_surface)

        targets = attack_surface.get("targets", [])
        return StageResult(
            success=True,
            output_count=len(targets),
            cost_usd=result.cost_usd,
            metadata={"total_targets": len(targets)},
        )
