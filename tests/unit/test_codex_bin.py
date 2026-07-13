"""Tier 0 — unit tests for the codex-binary override + reasoning-effort tolerance.

These cover the workaround that lets the web app use a newer system codex
(unlocking gpt-5.6 + max/ultra reasoning) with the pinned openai-codex SDK.
"""

from __future__ import annotations

from bug_hunter.core.cli_wrapper import resolve_codex_bin, tolerate_new_reasoning_efforts


def test_resolve_codex_bin_empty_is_bundled():
    assert resolve_codex_bin("") is None
    assert resolve_codex_bin(None) is None


def test_resolve_codex_bin_explicit_path():
    assert resolve_codex_bin("/opt/codex/bin/codex") == "/opt/codex/bin/codex"


def test_resolve_codex_bin_system(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    assert resolve_codex_bin("system") == "/usr/bin/codex"


def test_tolerate_new_reasoning_efforts_accepts_max_ultra():
    tolerate_new_reasoning_efforts()
    from openai_codex.generated.v2_all import ReasoningEffort
    # values the SDK ships with still resolve
    assert ReasoningEffort("xhigh").value == "xhigh"
    # values newer than the SDK knows no longer raise
    assert ReasoningEffort("max").value == "max"
    assert ReasoningEffort("ultra").value == "ultra"


def test_tolerate_is_idempotent():
    tolerate_new_reasoning_efforts()
    tolerate_new_reasoning_efforts()
    from openai_codex.generated.v2_all import ReasoningEffort
    assert ReasoningEffort("max").value == "max"
