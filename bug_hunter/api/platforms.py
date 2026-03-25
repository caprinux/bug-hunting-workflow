"""API routes for bug bounty platform integrations."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from fastapi import APIRouter, HTTPException

from bug_hunter.core.cli_wrapper import run_claude
from bug_hunter.platforms import registry

router = APIRouter(prefix="/api/platforms")


@router.get("")
async def api_list_platforms():
    """List available platform plugins."""
    platforms = registry.list_platforms()
    return [
        {
            "name": p.name,
            "display_name": p.display_name,
            "credential_fields": p.credential_fields,
            "last_scraped": p.last_scraped,
            "programs_count": len(p.list_programs()),
        }
        for p in platforms
    ]


@router.post("/{platform_name}/scrape")
async def api_scrape_platform(platform_name: str, credentials: dict):
    """Authenticate and scrape programs from a platform."""
    platform = registry.get(platform_name)
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform '{platform_name}' not found")

    result = await platform.scrape(credentials)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    return {
        "status": "success",
        "programs_count": result.programs_count,
    }


@router.get("/{platform_name}/programs")
async def api_list_programs(platform_name: str):
    """List programs from a platform's cached data."""
    platform = registry.get(platform_name)
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform '{platform_name}' not found")

    programs = platform.list_programs()
    return [dataclasses.asdict(p) for p in programs]


@router.get("/{platform_name}/programs/{program_id}")
async def api_get_program(platform_name: str, program_id: str):
    """Get full details for a specific program."""
    platform = registry.get(platform_name)
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform '{platform_name}' not found")

    program = platform.get_program(program_id)
    if not program:
        raise HTTPException(status_code=404, detail=f"Program '{program_id}' not found")

    result = dataclasses.asdict(program)
    # Don't send full raw_data to reduce payload size
    result.pop("raw_data", None)
    return result


@router.post("/{platform_name}/programs/{program_id}/import")
async def api_import_program(platform_name: str, program_id: str):
    """Use LLM to parse program data into engagement creation form fields."""
    platform = registry.get(platform_name)
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform '{platform_name}' not found")

    program = platform.get_program(program_id)
    if not program:
        raise HTTPException(status_code=404, detail=f"Program '{program_id}' not found")

    # Build prompt with raw program data
    raw_json = json.dumps(program.raw_data, indent=2, default=str)

    prompt = f"""Parse this bug bounty program data and extract the fields needed to create a security audit engagement.

PROGRAM DATA:
{raw_json[:50000]}

Extract and output a JSON object with these exact fields:
{{
  "name": "program name suitable for an engagement title",
  "qualifying_vulns": "one vulnerability type per line, extracted from qualifying_vulnerability list",
  "non_qualifying_vulns": "one vulnerability type per line, extracted from non_qualifying_vulnerability list",
  "assets_in_scope": "one asset per line with type (e.g., '*.example.com (web application, HIGH)')",
  "assets_not_in_scope": "one asset per line from out_of_scope list",
  "scope_notes": "any important rules, special conditions, or restrictions from rules_text",
  "additional_context": "any extra context worth noting for the bug hunter — VPN requirements, account access info, reward tiers, program-specific guidance",
  "source_repo": "any GitHub/source code URLs found in scopes (empty string if none)",
  "infra_url": "primary target URL if identifiable (empty string if none)",
  "credentials": "account access details if mentioned (empty string if none)"
}}

Be thorough — include all qualifying and non-qualifying vulnerability types.
For assets_in_scope, include the scope_type and asset_value for each entry.
For scope_notes, summarize the key rules — don't include the entire rules text."""

    result = await run_claude(
        prompt=prompt,
        model="sonnet",  # Fast model for structured extraction
        timeout=120,
    )

    if not result.success:
        raise HTTPException(status_code=500, detail=f"LLM import failed: {result.error}")

    parsed = result.result
    if isinstance(parsed, str):
        try:
            import re
            match = re.search(r'\{[\s\S]*\}', parsed)
            if match:
                parsed = json.loads(match.group())
            else:
                parsed = {}
        except (json.JSONDecodeError, TypeError):
            parsed = {}

    if not isinstance(parsed, dict):
        parsed = {}

    # Fallback: use raw program data if LLM failed to extract
    if not parsed.get("name"):
        parsed["name"] = program.name
    if not parsed.get("qualifying_vulns"):
        parsed["qualifying_vulns"] = "\n".join(program.qualifying_vulns)
    if not parsed.get("non_qualifying_vulns"):
        parsed["non_qualifying_vulns"] = "\n".join(program.non_qualifying_vulns)
    if not parsed.get("assets_in_scope"):
        parsed["assets_in_scope"] = "\n".join(
            f"{s.get('scope', '')} ({s.get('scope_type_name', '')}, {s.get('asset_value', '')})"
            for s in program.scopes
        )
    if not parsed.get("assets_not_in_scope"):
        parsed["assets_not_in_scope"] = "\n".join(program.out_of_scope)

    return parsed
