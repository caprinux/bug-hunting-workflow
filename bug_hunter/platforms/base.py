"""Base class for bug bounty platform integrations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ProgramSummary:
    """Minimal program info for listing."""
    id: str
    name: str
    platform: str
    bounty: bool = False
    reward_min: int = 0
    reward_max: int = 0
    scope_count: int = 0
    tags: list[str] = field(default_factory=list)
    status: str = ""


@dataclass
class ProgramDetails(ProgramSummary):
    """Full program info for import."""
    scopes: list[dict] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    qualifying_vulns: list[str] = field(default_factory=list)
    non_qualifying_vulns: list[str] = field(default_factory=list)
    rules_text: str = ""
    account_access: str = ""
    vpn_required: bool = False
    hunter_credentials: list = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)


@dataclass
class ScrapeResult:
    success: bool
    programs_count: int = 0
    error: str = ""


class BugBountyPlatform(ABC):
    """Base class for bug bounty platform plugins.

    Each plugin must implement scraping, listing, and detail retrieval.
    The output format (ProgramSummary, ProgramDetails) is standardized
    so the frontend and LLM import work identically across platforms.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Machine name (e.g., 'yeswehack')."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name (e.g., 'YesWeHack')."""
        ...

    @abstractmethod
    async def scrape(self, credentials: dict) -> ScrapeResult:
        """Authenticate and scrape programs from the platform.

        Args:
            credentials: Platform-specific auth (email, password, totp, etc.)
        """
        ...

    @abstractmethod
    def list_programs(self) -> list[ProgramSummary]:
        """List all cached programs."""
        ...

    @abstractmethod
    def get_program(self, program_id: str) -> Optional[ProgramDetails]:
        """Get full details for a specific program."""
        ...

    @property
    @abstractmethod
    def credential_fields(self) -> list[dict]:
        """Fields needed for authentication.

        Returns list of dicts with 'name', 'label', 'type' (text/password), 'required'.
        """
        ...

    @property
    def last_scraped(self) -> Optional[str]:
        """ISO timestamp of last successful scrape, or None."""
        return None
