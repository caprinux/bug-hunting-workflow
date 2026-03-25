"""YesWeHack platform integration."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

from bug_hunter.platforms.base import (
    BugBountyPlatform, ProgramDetails, ProgramSummary, ScrapeResult,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "audit_output" / "platforms" / "yeswehack"
PROGRAMS_FILE = DATA_DIR / "programs_raw.json"
# Also check the existing yeswehack tools data directory
_ALT_PROGRAMS_FILE = Path.home() / "yeswehack" / "data" / "programs_raw.json"


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self):
        return " ".join(self._parts).strip()


def _strip_html(html_str: str) -> str:
    if not html_str:
        return ""
    extractor = _HTMLTextExtractor()
    extractor.feed(html_str)
    return extractor.get_text()


class YesWeHackPlatform(BugBountyPlatform):

    @property
    def name(self) -> str:
        return "yeswehack"

    @property
    def display_name(self) -> str:
        return "YesWeHack"

    @property
    def credential_fields(self) -> list[dict]:
        return [
            {"name": "email", "label": "Email", "type": "text", "required": True},
            {"name": "password", "label": "Password", "type": "password", "required": True},
            {"name": "totp", "label": "TOTP Code", "type": "text", "required": False},
        ]

    @property
    def last_scraped(self) -> Optional[str]:
        data_file = self._get_data_file()
        if not data_file:
            return None
        try:
            with open(data_file) as f:
                data = json.load(f)
            return data.get("fetched_at")
        except Exception:
            return None

    async def scrape(self, credentials: dict) -> ScrapeResult:
        """Authenticate to YWH and fetch all accessible programs."""
        email = credentials.get("email", "")
        password = credentials.get("password", "")
        totp = credentials.get("totp", "")

        if not email or not password:
            return ScrapeResult(success=False, error="Email and password are required")

        try:
            from yeswehack.api import YesWeHack

            ywh = YesWeHack(username=email, password=password, lazy=True)

            # Run login in a thread to avoid blocking async
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, lambda: ywh.login(totp_code=totp or None))
            except Exception as e:
                if "Totp login is enable" in str(e) and not totp:
                    return ScrapeResult(success=False, error="TOTP code is required for this account")
                return ScrapeResult(success=False, error=f"Login failed: {e}")

            logger.info("YWH login successful, fetching programs...")

            # Fetch program list
            programs = []
            page = 1
            while True:
                resp = await loop.run_in_executor(
                    None, lambda p=page: ywh.call("GET", f"/programs?page={p}")
                )
                items = resp.get("items", [])
                if not items:
                    break

                for p in items:
                    if p.get("disabled"):
                        continue
                    programs.append({
                        "slug": p.get("slug"),
                        "title": p.get("title"),
                        "bounty": p.get("bounty"),
                        "bounty_reward_min": p.get("bounty_reward_min"),
                        "bounty_reward_max": p.get("bounty_reward_max"),
                    })

                nb_pages = resp.get("pagination", {}).get("nb_pages", 1)
                if page >= nb_pages:
                    break
                page += 1

            logger.info(f"Found {len(programs)} programs, fetching details...")

            # Fetch details for each program
            detailed = []
            for i, summary in enumerate(programs):
                slug = summary["slug"]
                try:
                    resp = await loop.run_in_executor(
                        None, lambda s=slug: ywh.call("GET", f"/programs/{s}")
                    )
                    detailed.append({
                        "slug": resp.get("slug"),
                        "title": resp.get("title"),
                        "public": resp.get("public"),
                        "bounty": resp.get("bounty"),
                        "disabled": resp.get("disabled"),
                        "status": resp.get("status"),
                        "bounty_reward_min": resp.get("bounty_reward_min"),
                        "bounty_reward_max": resp.get("bounty_reward_max"),
                        "vpn_active": resp.get("vpn_active"),
                        "account_access": resp.get("account_access"),
                        "rules_text": _strip_html(resp.get("rules_html", "")),
                        "scopes": resp.get("scopes", []),
                        "out_of_scope": resp.get("out_of_scope", []),
                        "qualifying_vulnerability": resp.get("qualifying_vulnerability", []),
                        "non_qualifying_vulnerability": resp.get("non_qualifying_vulnerability", []),
                        "tags": resp.get("tags", []),
                        "reward_grid_default": resp.get("reward_grid_default"),
                        "reward_grid_high": resp.get("reward_grid_high"),
                        "business_unit": resp.get("business_unit", {}),
                    })
                except Exception as e:
                    logger.warning(f"Failed to fetch {slug}: {e}")

                # Rate limiting
                await asyncio.sleep(0.5)

            # Save to disk
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            output = {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "total": len(detailed),
                "programs": detailed,
            }
            with open(PROGRAMS_FILE, "w") as f:
                json.dump(output, f, indent=2, default=str)

            logger.info(f"Saved {len(detailed)} programs to {PROGRAMS_FILE}")
            return ScrapeResult(success=True, programs_count=len(detailed))

        except ImportError:
            return ScrapeResult(success=False, error="yeswehack Python package not installed. Run: pip install yeswehack")
        except Exception as e:
            return ScrapeResult(success=False, error=str(e))

    def _get_data_file(self) -> Optional[Path]:
        """Find the programs data file — check primary and fallback locations."""
        if PROGRAMS_FILE.exists():
            return PROGRAMS_FILE
        if _ALT_PROGRAMS_FILE.exists():
            return _ALT_PROGRAMS_FILE
        return None

    def list_programs(self) -> list[ProgramSummary]:
        """List programs from cached data."""
        data_file = self._get_data_file()
        if not data_file:
            return []

        try:
            with open(data_file) as f:
                data = json.load(f)
        except Exception:
            return []

        programs = []
        for p in data.get("programs", []):
            programs.append(ProgramSummary(
                id=p.get("slug", ""),
                name=p.get("title", ""),
                platform="yeswehack",
                bounty=p.get("bounty", False),
                reward_min=p.get("bounty_reward_min", 0) or 0,
                reward_max=p.get("bounty_reward_max", 0) or 0,
                scope_count=len(p.get("scopes", [])),
                tags=[t.get("name", t) if isinstance(t, dict) else str(t) for t in (p.get("tags") or [])],
                status=p.get("status", ""),
            ))

        return programs

    def get_program(self, program_id: str) -> Optional[ProgramDetails]:
        """Get full details for a specific program."""
        data_file = self._get_data_file()
        if not data_file:
            return None

        try:
            with open(data_file) as f:
                data = json.load(f)
        except Exception:
            return None

        for p in data.get("programs", []):
            if p.get("slug") == program_id:
                return ProgramDetails(
                    id=p.get("slug", ""),
                    name=p.get("title", ""),
                    platform="yeswehack",
                    bounty=p.get("bounty", False),
                    reward_min=p.get("bounty_reward_min", 0) or 0,
                    reward_max=p.get("bounty_reward_max", 0) or 0,
                    scope_count=len(p.get("scopes", [])),
                    tags=[t.get("name", t) if isinstance(t, dict) else str(t) for t in (p.get("tags") or [])],
                    status=p.get("status", ""),
                    scopes=p.get("scopes", []),
                    out_of_scope=p.get("out_of_scope", []),
                    qualifying_vulns=p.get("qualifying_vulnerability", []),
                    non_qualifying_vulns=p.get("non_qualifying_vulnerability", []),
                    rules_text=p.get("rules_text", ""),
                    account_access=p.get("account_access", ""),
                    vpn_required=p.get("vpn_active", False),
                    raw_data=p,
                )

        return None
