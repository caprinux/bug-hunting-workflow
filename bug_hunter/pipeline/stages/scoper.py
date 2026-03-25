"""Scoper stage — understand the target, map attack surfaces, identify scope."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from bug_hunter.core.cli_wrapper import run_claude
from bug_hunter.core.events import event_manager
from bug_hunter.utils.result_parser import parse_agent_result
from bug_hunter.pipeline.stages.base import PipelineStage, StageContext, StageResult
from bug_hunter.pipeline.stages.registry import register

logger = logging.getLogger(__name__)
AGENTS_DIR = Path(__file__).parent.parent.parent.parent / "agents"


@register
class ScoperStage(PipelineStage):

    @property
    def name(self) -> str:
        return "scoper"

    async def execute(self, context: StageContext) -> StageResult:
        stage_dir = self.get_stage_dir(context)
        eng_config = context.engagement["config"]
        eng_type = context.engagement["type"]
        scope_def = eng_config.get("engagement", {}).get("scope_definition", "")

        # Get source path from setup
        setup_data = self.read_previous_output(context, "setup", "setup.json")
        source_path = ""
        if setup_data and "source" in setup_data:
            source_path = setup_data["source"]["local_path"]
        if not source_path:
            source_path = eng_config.get("engagement", {}).get("source_path", "")

        if eng_type == "source_code" and not source_path:
            return StageResult(success=False, error="No source path available")

        await event_manager.emit_log(
            context.engagement_id, context.run_id, self.name,
            "Mapping target architecture and attack surfaces...",
        )

        if eng_type == "source_code":
            prompt = self._build_source_code_prompt(source_path, scope_def)
            agent_file = str(AGENTS_DIR / "source_code" / "scoper.md")
            cwd = source_path
        else:
            domains = eng_config.get("engagement", {}).get("target_domains", [])
            infra_config = eng_config.get("engagement", {}).get("infra_config", "")
            prompt = self._build_black_box_prompt(domains, scope_def, infra_config)
            agent_file = str(AGENTS_DIR / "black_box" / "scoper.md")
            cwd = None

        record_dir, record_meta = self.prepare_agent_run(
            context, "claude", "scoper",
            {"model": context.config.models.scoper, "engagement_type": eng_type},
        )

        result = await run_claude(
            prompt=prompt,
            agent_file=agent_file,
            model=context.config.models.scoper,
            cwd=cwd,
            timeout=context.config.pipeline.subagent_timeout,
            record_dir=record_dir,
            record_metadata=record_meta,
        )

        if not result.success:
            return StageResult(success=False, error=result.error, cost_usd=result.cost_usd)

        scope_data = parse_agent_result(result.result, ['attack_surfaces', 'architecture'], "scoper")
        self.write_output(context, "scope.json", scope_data)

        attack_surfaces = scope_data.get("attack_surfaces", [])
        return StageResult(
            success=True,
            output_count=len(attack_surfaces),
            cost_usd=result.cost_usd,
            metadata={"attack_surfaces": len(attack_surfaces)},
        )

    def _build_source_code_prompt(self, source_path: str, scope_def: str) -> str:
        return f"""You are analyzing a codebase to understand its architecture and map all attack surfaces.

SOURCE CODE ROOT: {source_path}

SCOPE DEFINITION:
{scope_def or "All code is in scope. Focus on security-relevant functionality."}

INSTRUCTIONS:
1. Read the directory structure, entry points, configuration files, route definitions, and middleware
2. Understand the application architecture — what it does, how it's structured, what frameworks it uses
3. Identify ALL attack surfaces:
   - HTTP endpoints and their parameters
   - Authentication and authorization mechanisms
   - File upload/download handlers
   - Database queries and ORM usage
   - External API integrations
   - Deserialization points
   - Command execution paths
   - Cryptographic operations
   - Session management
   - Input validation boundaries
4. For each attack surface, note:
   - Location (file paths, line ranges)
   - What it does
   - What user input it accepts
   - Why it might be vulnerable
   - Priority (high/medium/low based on attack potential)
5. Note qualifying and non-qualifying vulnerability types from the scope

Output a JSON object:
{{
  "architecture": {{
    "description": "Brief description of what this application does",
    "framework": "The web framework or technology stack",
    "entry_points": ["List of main entry point files"],
    "key_modules": ["List of security-relevant modules/directories"]
  }},
  "attack_surfaces": [
    {{
      "id": "surface-001",
      "name": "User Authentication",
      "location": ["src/auth/login.py", "src/auth/jwt.py"],
      "description": "JWT-based authentication with password login",
      "inputs": "username, password via POST /api/login",
      "priority": "high",
      "potential_vulns": ["auth bypass", "JWT manipulation", "brute force"],
      "status": "not_scanned"
    }}
  ],
  "scope_notes": {{
    "qualifying": ["Types of vulns that are in scope"],
    "non_qualifying": ["Types of vulns that are out of scope"],
    "excluded_paths": ["Paths/components explicitly excluded"]
  }}
}}"""

    def _build_black_box_prompt(self, domains: list, scope_def: str, infra_config: str) -> str:
        return f"""You are performing reconnaissance on target domains to map the complete attack surface.

TARGET DOMAINS: {json.dumps(domains)}

SCOPE DEFINITION:
{scope_def or "All domains listed are in scope."}

INFRASTRUCTURE ACCESS:
{infra_config}

INSTRUCTIONS:
1. Use reconnaissance tools to enumerate the target:
   - Passive: certificate transparency (crt.sh), DNS records, WHOIS
   - Active: subdomain brute-forcing (subfinder), port scanning (nmap), HTTP probing (httpx)
2. For each discovered target, identify:
   - Live endpoints and their technology stack
   - Authentication mechanisms
   - API endpoints and parameters
   - File upload features
   - Admin panels or debug endpoints
3. Produce a structured attack surface map

CRITICAL: Output ONLY a JSON object with this exact structure:
{{
  "architecture": {{
    "description": "What this application does",
    "framework": "Technology stack",
    "entry_points": ["main entry point URLs or files"],
    "key_modules": ["security-relevant components"]
  }},
  "attack_surfaces": [
    {{
      "id": "surface-001",
      "name": "Surface name",
      "location": ["URLs or endpoints"],
      "description": "What it does",
      "inputs": "What user input it accepts",
      "priority": "high|medium|low",
      "potential_vulns": ["possible vulnerability types"],
      "status": "not_scanned"
    }}
  ],
  "scope_notes": {{
    "qualifying": ["qualifying vuln types"],
    "non_qualifying": ["non-qualifying vuln types"],
    "excluded_paths": ["excluded targets"]
  }}
}}"""
