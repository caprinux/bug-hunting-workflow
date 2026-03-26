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


_scrape_status: dict[str, dict] = {}


@router.post("/{platform_name}/scrape")
async def api_scrape_platform(platform_name: str, credentials: dict):
    """Start scraping programs from a platform (runs in background)."""
    platform = registry.get(platform_name)
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform '{platform_name}' not found")

    if _scrape_status.get(platform_name, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="Scrape already in progress")

    _scrape_status[platform_name] = {"status": "running", "message": "Authenticating..."}

    import asyncio

    async def _run_scrape():
        try:
            _scrape_status[platform_name] = {"status": "running", "message": "Authenticating and fetching programs...", "progress": 0, "total": 0}

            # Pass a progress callback to the scraper
            def on_progress(current, total, slug=""):
                _scrape_status[platform_name] = {
                    "status": "running",
                    "message": f"Fetching {current}/{total}: {slug}",
                    "progress": current,
                    "total": total,
                }

            result = await platform.scrape(credentials, on_progress=on_progress)
            if result.success:
                _scrape_status[platform_name] = {
                    "status": "completed",
                    "message": f"Scraped {result.programs_count} programs",
                    "programs_count": result.programs_count,
                }
            else:
                _scrape_status[platform_name] = {"status": "failed", "message": result.error}
        except Exception as e:
            _scrape_status[platform_name] = {"status": "failed", "message": str(e)}

    asyncio.create_task(_run_scrape())
    return {"status": "started"}


@router.get("/{platform_name}/scrape/status")
async def api_scrape_status(platform_name: str):
    """Check the status of an ongoing scrape."""
    return _scrape_status.get(platform_name, {"status": "idle"})


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


_import_status: dict[str, dict] = {}


@router.post("/{platform_name}/programs/{program_id}/import")
async def api_import_program(platform_name: str, program_id: str):
    """Start LLM import of program data (runs in background)."""
    platform = registry.get(platform_name)
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform '{platform_name}' not found")

    program = platform.get_program(program_id)
    if not program:
        raise HTTPException(status_code=404, detail=f"Program '{program_id}' not found")

    import_key = f"{platform_name}/{program_id}"
    if _import_status.get(import_key, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="Import already in progress")

    _import_status[import_key] = {"status": "running", "message": "Parsing program data with LLM..."}

    import asyncio

    async def _run_import():
        try:
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
  "source_repo": "any GitHub/GitLab/source code repository URLs found in scopes OR rules_text, comma-separated if multiple (e.g., 'https://gitlab.com/org/repo1, https://github.com/org/repo2'). Look for markdown links like [name](url) in rules_text. Empty string if none.",
  "infra_url": "primary target URL if identifiable (empty string if none)",
  "credentials": "extract ALL credentials from hunter_credentials field. Format each as 'role: username / password'. Include all accounts, API keys, tokens, and access details. Empty string if none."
}}

Be thorough — include all qualifying and non-qualifying vulnerability types.
For assets_in_scope, include the scope_type and asset_value for each entry.
For scope_notes, summarize the key rules — don't include the entire rules text."""

            result = await run_claude(
                prompt=prompt,
                model="sonnet",
                timeout=120,
            )

            if not result.success:
                # Fallback to raw data
                parsed = {}
            else:
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

            # Fallback fields from raw program data
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

            _import_status[import_key] = {"status": "completed", "result": parsed}
        except Exception as e:
            _import_status[import_key] = {"status": "failed", "message": str(e)}

    asyncio.create_task(_run_import())
    return {"status": "started"}


@router.get("/{platform_name}/programs/{program_id}/import/status")
async def api_import_status(platform_name: str, program_id: str):
    """Check the status of a program import."""
    import_key = f"{platform_name}/{program_id}"
    return _import_status.get(import_key, {"status": "idle"})
