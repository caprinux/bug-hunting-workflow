# Tests

Four tiers. Tiers 0–2 are **offline, deterministic, and free** — no network, no
LLM, no cost — and run in a few seconds. Tier 3 is opt-in and calls real models.

```
tests/
├── conftest.py            # temp DB + temp output dir, config/engagement fixtures
├── unit/                  # Tier 0 — pure functions
├── pipeline/              # Tier 1 — orchestrator + stages via the replay backend
│   └── replay.py          #          the fake agent backend (canned CLIResults)
├── api/                   # Tier 2 — REST + WebSocket against the real app
├── live/                  # Tier 3 — real Claude + Codex (opt-in)
└── fixtures/vuln_app/     #          deliberately-vulnerable app for the live tier
```

## Running

```bash
pip install -r requirements-dev.txt

pytest                         # tiers 0–2 (live is auto-skipped)
pytest tests/unit              # just the fast unit tier
RUN_LIVE_E2E=1 pytest -m live -s   # tier 3 (needs authenticated claude + codex)
```

## How the offline tiers avoid real LLM calls

Every stage imports its agent runner (`run_claude` / `run_codex` / `run_agent`)
into its own module namespace, so that is the single seam where non-determinism
enters. `tests/pipeline/replay.py` monkeypatches those per-stage symbols with
fakes that return canned `CLIResult`s. The **real** orchestrator, stages, schema
validation, SQLite persistence, checkpoint/resume, API routes, and WebSocket
broadcasting all run unchanged.

Canned results are hand-authored to match each stage's exact parser contract
(e.g. `strict_validator` only marks a bug validated if its PoC has non-empty
`code`; findings must satisfy `bug_finding.json`'s required fields). See
`replay.py` for the helpers (`canned_bug`, `validated_poc`).

## What each tier covers

- **Tier 0 (`unit/`)** — the Codex serialization/merge helpers (`_serialize_codex_event`,
  `_merge_codex_messages`, `split_codex_agent_message`) including a round-trip
  through the `routes.py` stream-replay parser, and the persistent-session
  helpers that drive Claude/Codex resume.
- **Tier 1 (`pipeline/`)** — a full source-code pipeline run to completion with
  confirmed bugs + on-disk artifacts; the Codex thread / Claude session resume
  across re-hunts (including the resume-failure retry); and cancel→resume
  checkpointing (completed stages are not re-run).
- **Tier 2 (`api/`)** — create engagement, start run (awaited to completion),
  run status, bug listing, the stage-stream endpoint's `codex_event` parser, and
  WebSocket auth + scoped/global broadcast filtering.
- **Tier 3 (`live/`)** — runs the real pipeline (Claude **and** Codex) against
  the planted-vuln fixture and asserts, with tolerance, that it finds SQLi/IDOR.

## Adding a new stage cassette

If a stage's parser changes, update its canned result in `replay.py` (or set a
per-test override via `backend.set("<stage>", {...})`). Real recorded outputs
also live under each run's `agent_runs/*/result.json` if you want to derive a
cassette from a live run.
