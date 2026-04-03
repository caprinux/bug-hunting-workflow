"""API routes for bug bounty platform integrations."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from fastapi import APIRouter, HTTPException

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
    """Import program data directly — no LLM needed."""
    platform = registry.get(platform_name)
    if not platform:
        raise HTTPException(status_code=404, detail=f"Platform '{platform_name}' not found")

    program = platform.get_program(program_id)
    if not program:
        raise HTTPException(status_code=404, detail=f"Program '{program_id}' not found")

    import_key = f"{platform_name}/{program_id}"

    try:
        # Build scope definition from structured fields
        scope_parts = []

        if program.qualifying_vulns:
            scope_parts.append("QUALIFYING VULNERABILITIES:\n" + "\n".join(program.qualifying_vulns))
        if program.non_qualifying_vulns:
            scope_parts.append("NON-QUALIFYING VULNERABILITIES:\n" + "\n".join(program.non_qualifying_vulns))
        if program.scopes:
            assets = "\n".join(
                f"{s.get('scope', '')} ({s.get('scope_type_name', '')}, {s.get('asset_value', '')})"
                for s in program.scopes
            )
            scope_parts.append("ASSETS IN SCOPE:\n" + assets)
        if program.out_of_scope:
            scope_parts.append("ASSETS NOT IN SCOPE:\n" + "\n".join(program.out_of_scope))
        if program.rules_text:
            scope_parts.append("ADDITIONAL NOTES:\n" + program.rules_text)

        # Extract credentials
        credentials = ""
        if program.hunter_credentials:
            cred_lines = []
            for cred in program.hunter_credentials:
                if isinstance(cred, dict):
                    cred_lines.append(
                        f"{cred.get('access_type', '')}: {cred.get('login', '')} / {cred.get('password', '')}"
                    )
                else:
                    cred_lines.append(str(cred))
            credentials = "\n".join(cred_lines)

        # Extract target domains from scopes
        target_domains = []
        for s in program.scopes:
            scope_val = s.get("scope", "")
            if scope_val and s.get("scope_type_name", "").lower() in ("web application", "api", "web-application"):
                target_domains.append(scope_val)

        parsed = {
            "name": program.name,
            "qualifying_vulns": "\n".join(program.qualifying_vulns),
            "non_qualifying_vulns": "\n".join(program.non_qualifying_vulns),
            "assets_in_scope": "\n".join(
                f"{s.get('scope', '')} ({s.get('scope_type_name', '')}, {s.get('asset_value', '')})"
                for s in program.scopes
            ),
            "assets_not_in_scope": "\n".join(program.out_of_scope),
            "scope_notes": "\n\n".join(scope_parts),
            "additional_context": program.account_access or "",
            "source_repo": "",
            "infra_url": target_domains[0] if target_domains else "",
            "credentials": credentials,
            "target_domains": target_domains,
            "raw_program_data": program.raw_data,
        }

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
