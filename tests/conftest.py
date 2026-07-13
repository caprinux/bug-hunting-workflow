"""Shared pytest fixtures.

Everything here is offline and deterministic: a per-test temp SQLite DB and a
per-test temp output directory, plus helpers to build an AppConfig and seed an
engagement. No fixture here touches the network or a real LLM — the live tier
(tests/live) opts into that explicitly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Importing the stages package registers every PipelineStage in the registry.
import bug_hunter.pipeline.stages  # noqa: F401  (import for side effect)
from bug_hunter.core import database
from bug_hunter.core.config import AppConfig
from bug_hunter.core.database import create_engagement, init_db


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    """A temp audit_output directory for a single test."""
    out = tmp_path / "audit_output"
    out.mkdir(parents=True, exist_ok=True)
    return out


@pytest.fixture
def db(tmp_path: Path):
    """Initialize an isolated SQLite DB for the test and reset the global after."""
    prev = database.DB_PATH
    init_db(str(tmp_path / "db.sqlite"))
    try:
        yield
    finally:
        database.DB_PATH = prev


@pytest.fixture
def app_config(tmp_output: Path) -> AppConfig:
    """A minimal AppConfig pointed at the test's temp output dir.

    Defaults keep the pipeline small and offline-friendly; individual tests
    tweak the returned object before handing it to the orchestrator.
    """
    cfg = AppConfig()
    cfg.pipeline.output_dir = str(tmp_output)
    cfg.pipeline.auto_install_tools = False  # never shell out to installers in tests
    cfg.pipeline.resume = True
    cfg.pipeline.retry_limit = 1
    cfg.pipeline.subagent_timeout = 5
    return cfg


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """A tiny source tree the setup stage can 'acquire' via a local path."""
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True, exist_ok=True)
    (repo / "app" / "views.py").write_text(
        "def get_user(uid):\n"
        "    # deliberately naive lookup for the fixture\n"
        "    return db.query(f\"SELECT * FROM users WHERE id = {uid}\")\n"
    )
    (repo / "README.md").write_text("# fixture app\n")
    return repo


@pytest.fixture
def make_engagement(db):
    """Factory: create a source_code engagement whose config points at a repo.

    Returns the engagement dict (as create_engagement/get_engagement return it).
    """
    def _make(source_path: str, *, name: str = "test-eng",
              eng_type: str = "source_code", scope: str = "All code in app/",
              overrides: dict | None = None) -> dict:
        config: dict = {
            "engagement": {
                "type": eng_type,
                "source_path": source_path,
                "source_repo": "",
                "target_domains": [],
                "scope_definition": scope,
                "infra_config": "",
            },
        }
        if overrides:
            for k, v in overrides.items():
                config.setdefault(k, {})
                config[k].update(v) if isinstance(v, dict) else config.update({k: v})
        return create_engagement(name, eng_type, config)
    return _make


@pytest.fixture(autouse=True)
def _reset_event_manager():
    """Clear the module-global event_manager connection state between tests."""
    from bug_hunter.core.events import event_manager
    yield
    for attr in ("_connections", "_global_connections"):
        obj = getattr(event_manager, attr, None)
        if isinstance(obj, dict):
            obj.clear()
        elif isinstance(obj, set):
            obj.clear()
