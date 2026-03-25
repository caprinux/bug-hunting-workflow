"""Platform plugin registry."""

from __future__ import annotations

from typing import Optional

from bug_hunter.platforms.base import BugBountyPlatform

_PLATFORMS: dict[str, BugBountyPlatform] = {}


def register(platform: BugBountyPlatform) -> None:
    _PLATFORMS[platform.name] = platform


def get(name: str) -> Optional[BugBountyPlatform]:
    return _PLATFORMS.get(name)


def list_platforms() -> list[BugBountyPlatform]:
    return list(_PLATFORMS.values())


# Auto-register available platforms
def _auto_register():
    try:
        from bug_hunter.platforms.yeswehack import YesWeHackPlatform
        register(YesWeHackPlatform())
    except Exception:
        pass


_auto_register()
