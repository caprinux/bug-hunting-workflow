"""Fixtures for the API tier — an in-process app wired to a temp DB + output dir,
with the replay agent backend installed so `start run` never hits a real LLM.
"""

from __future__ import annotations

import pytest

from bug_hunter.core.config import load_config

from tests.pipeline.replay import ReplayBackend, canned_bug, validated_poc


@pytest.fixture
def temp_config(tmp_output):
    cfg = load_config()
    cfg.pipeline.output_dir = str(tmp_output)
    cfg.pipeline.auto_install_tools = False
    cfg.pipeline.subagent_timeout = 5
    cfg.pipeline.retry_limit = 1
    return cfg


@pytest.fixture
def api(monkeypatch, db, temp_config):
    """Import the app, point routes at the temp config/DB, clear global state,
    and install the replay backend. Returns the ASGI app object."""
    import bug_hunter.main as main
    import bug_hunter.api.routes as routes
    from bug_hunter.core.auth import set_auth_password

    # No auth: leave the password empty so verify_credentials returns True.
    monkeypatch.delenv("BHW_PASSWORD", raising=False)
    set_auth_password("")

    # All output_dir resolution in routes flows through this.
    monkeypatch.setattr(routes, "load_config", lambda *a, **k: temp_config)

    # Fresh orchestrator registry per test.
    routes._orchestrators.clear()

    # Replay backend for any run started through the API.
    backend = ReplayBackend()
    backend.set("bug_hunter:claude", {"bugs": [canned_bug("SQL Injection")]})
    backend.set("bug_hunter:codex", {"bugs": [canned_bug("IDOR")]})
    backend.set("skills_hunter", {"narrative": "", "bugs": []})
    backend.set("variant_hunter", {"narrative": "", "bugs": []})
    backend.set("deduplicator", {"narrative": "", "deduplicated": [], "duplicate_groups": []})
    backend.set("scope_validator", {"narrative": "", "in_scope": [], "out_of_scope": []})
    backend.set("strict_validator", {
        "narrative": "", "validated": True, "verdict": "confirmed",
        "poc": validated_poc(), "reason": "",
    })
    backend.set("strict_triager", {"narrative": "", "tagged": []})
    backend.install(monkeypatch)

    main.app.state.replay_backend = backend
    return main.app
