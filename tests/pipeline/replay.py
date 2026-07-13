"""Replay agent backend for offline pipeline tests.

The entire pipeline's non-determinism enters through the agent-runner functions
in cli_wrapper (``run_claude`` / ``run_codex`` / ``run_agent``), which each stage
imports into its own module namespace. This module patches those per-stage
symbols with fakes that return canned ``CLIResult``s — so the *real* orchestrator
and *real* stages run end-to-end with no network, no LLM, and no cost.

Every agent call is recorded in ``backend.calls`` for assertions (e.g. verifying
the Codex thread id is threaded through on a re-hunt).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from bug_hunter.core.cli_wrapper import CLIResult

# stage name -> (module path, agent symbol). bug_hunter is handled separately
# because it calls two symbols (run_claude + run_codex).
_STAGE_AGENT_SYMBOL = {
    "scoper": "run_agent",
    "skills_hunter": "run_agent",
    "variant_hunter": "run_agent",
    "deduplicator": "run_agent",
    "scope_validator": "run_agent",
    "strict_validator": "run_agent",
    "perfectionist": "run_agent",
    "strict_triager": "run_agent",
    "bug_chainer": "run_agent",
    "testing_setup": "run_agent",
    "summarizer": "run_claude",
}


def canned_bug(vuln_type: str = "SQL Injection", source_file: str = "app/views.py") -> dict:
    """A finding that passes validate_findings_list (bug_finding.json required set)
    once the stage injects id + found_by. Includes source_file/vuln_type so the
    variant hunter treats it as patternable."""
    return {
        "source_file": source_file,
        "line_range": "1-3",
        "url": "",
        "http_evidence": "",
        "vuln_class": "CWE-89",
        "vuln_type": vuln_type,
        "description": f"{vuln_type}: user input flows into a sink unsanitized",
        "reasoning": "tainted parameter reaches the query without parameterization",
        "confidence": "high",
        "root_cause": "string interpolation into SQL",
        "security_impact": "database read/exfiltration",
        "validated": False,
        "poc": {"language": "python", "code": "", "execution_result": "failure", "output": ""},
    }


def validated_poc() -> dict:
    return {
        "language": "python",
        "code": "print('exploit ran')",
        "execution_result": "success",
        "output": "exploit ran",
    }


@dataclass
class ReplayBackend:
    # key -> result dict returned as CLIResult.result. Keys: stage name, or
    # "bug_hunter:claude" / "bug_hunter:codex".
    results: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, dict]] = field(default_factory=list)
    # key -> error string returned (as a failed CLIResult) exactly once. Mirrors
    # how the real run_claude/run_codex/run_agent surface errors — they return a
    # failed CLIResult rather than raising.
    fail_once: dict[str, str] = field(default_factory=dict)

    def set(self, key: str, result: Any) -> "ReplayBackend":
        self.results[key] = result
        return self

    def _make_fake(self, key: str) -> Callable:
        async def fake(*args, **kwargs):
            self.calls.append((key, kwargs))
            if key in self.fail_once:
                error = self.fail_once.pop(key)
                return CLIResult(success=False, error=error, raw_output="",
                                 session_id=kwargs.get("thread_id") or kwargs.get("session_id") or "")
            result = self.results.get(key, {})
            # Echo back a session/thread id so persistent-session stages save it.
            # If the caller passed one (a resume), return the same id.
            sid = (
                kwargs.get("thread_id")
                or kwargs.get("session_id")
                or f"{key.replace(':', '-')}-sess"
            )
            return CLIResult(success=True, result=result, session_id=sid,
                             usage={"input_tokens": 10, "output_tokens": 5})
        return fake

    def calls_for(self, key: str) -> list[dict]:
        return [kw for k, kw in self.calls if k == key]

    def install(self, monkeypatch) -> "ReplayBackend":
        # bug_hunter: two symbols on its own module
        monkeypatch.setattr(
            "bug_hunter.pipeline.stages.bug_hunter.run_claude",
            self._make_fake("bug_hunter:claude"),
        )
        monkeypatch.setattr(
            "bug_hunter.pipeline.stages.bug_hunter.run_codex",
            self._make_fake("bug_hunter:codex"),
        )
        for stage, symbol in _STAGE_AGENT_SYMBOL.items():
            monkeypatch.setattr(
                f"bug_hunter.pipeline.stages.{stage}.{symbol}",
                self._make_fake(stage),
                raising=True,
            )
        return self
