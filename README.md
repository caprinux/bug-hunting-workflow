# Bug Hunting Workflow

An automated security vulnerability discovery platform powered by LLM agents. It orchestrates specialized AI agents through a multi-stage pipeline to find, validate, expand, triage, and chain security bugs — then presents everything through an interactive web interface.

## What It Does

You point it at a codebase or a set of web domains. The platform deploys a team of specialized AI agents, each with a distinct role in the vulnerability discovery lifecycle:

### The Agents

**Scoper** — First agent to run. Quickly reads the codebase structure (or performs recon on black-box targets) and produces a prioritized attack surface map. Understands the architecture, identifies all entry points, maps security-relevant modules, and notes qualifying/non-qualifying vulnerability types from the scope definition. Runs once — the Bug Hunter reuses its output across iterations.

**Bug Hunter** — The core agent. Given the Scoper's attack surface map, it hunts for vulnerabilities freely — reading code, tracing data flows, following interesting leads across files. It updates two structured files as it works: `attack_surfaces.json` (marks surfaces as scanned, adds new ones discovered) and `BUGS.json` (documents each bug with root cause, security impact, PoC, and validation status). Can run for multiple iterations — each iteration reads previous progress and continues into unexplored areas. Multiple models (Claude, Codex) can hunt concurrently.

**De-duplicator** *(optional)* — When multiple agents (Claude + Codex) hunt concurrently, they may flag the same bugs independently. The De-duplicator merges duplicate findings while preserving distinct bugs at different locations. Multi-agent agreement is a confidence signal.

**Validator** — Quick verification pass. The Bug Hunter already attempts to validate its own findings with PoCs. The Validator confirms that each PoC actually works. For bugs without PoCs or with failed validation, it writes and executes its own. Destructive PoCs are never executed — flagged as "likely exploitable, PoC destructive."

**Perfectionist** — Given a validated bug, pushes the exploitation primitive to its absolute maximum. SQLi read becomes SQLi write becomes RCE. SSRF becomes cloud credential theft becomes account takeover. Each escalation step is demonstrated via live PoC execution. Single-bug expansion only.

**Triager** — Acts as a bug bounty triager. Strictly judges each bug against the scope definition and categorizes it: valid (real security impact, in scope), informational (useful intelligence but not a bug), out of scope (real bug, wrong target), or discarded (false positive, contrived). Fails closed — if the triager fails, bugs go to a review queue, never silently promoted.

**Bug Chainer** — Takes all confirmed bugs across all runs, reads the intelligence file for context, and constructs exploit chains that combine multiple bugs for maximum combined impact. Suggests specific re-hunt targets that require human approval before executing.

### Two Modes

**Source Code Audit** — Feed it a local codebase or a GitHub repo. The Scoper maps the architecture, then the Bug Hunter audits it with full freedom to follow leads. Multiple models can hunt concurrently.

**Black Box Pentest** — Give it target domains (including wildcards). The Scoper performs recon, then the Bug Hunter tests each attack surface using tools like curl, sqlmap, ffuf, or a headless browser.

### The Pipeline

```
  [Source Code or Target Domains]
       │
       ▼
  ┌──────────────────┐
  │     SCOPER       │  map architecture + attack surfaces
  └────────┬─────────┘
           ▼
  ┌──────────────────┐
  │   BUG HUNTER     │  free-form hunting, updates BUGS.json
  │                  │  iterative — re-hunt to continue
  └────────┬─────────┘
           ▼
  ┌──────────────────┐
  │  DE-DUPLICATOR   │  [optional, if multiple agents]
  └────────┬─────────┘
           ▼
  ┌──────────────────┐
  │    VALIDATOR     │  quick pass — verify PoCs work
  └────────┬─────────┘  cannot validate ──→ OUTPUT #1
           ▼
  ┌──────────────────┐
  │  PERFECTIONIST   │  expand single-bug primitives
  └────────┬─────────┘
           ▼
  ┌──────────────────┐
  │    TRIAGER       │  valid ──→ continue
  └────────┬─────────┘  informational ──→ OUTPUT #3
           │            out of scope / discarded ──→ logged
           ▼
  ┌──────────────────┐
  │   BUG CHAINER    │  chains + re-hunt suggestions
  └────────┬─────────┘
           ▼
      [FINAL REPORT] ──→ OUTPUT #2
           │
     (human decision)
     re-hunt? ──→ Bug Hunter reads BUGS.json, continues
```

### Outputs

Three outputs per engagement (cumulative across runs):

1. **Cannot-validate bin** — Bugs that couldn't be proven, with reasons. Includes "likely exploitable but PoC destructive." For human review.
2. **Final report** — Confirmed bugs with PoCs, expanded primitives, demonstrated and proposed chains, re-hunt suggestions.
3. **Intelligence file** — Informational findings (internal IPs, versions, architecture details). Feeds the Bug Chainer for chain construction.

Every stage writes structured JSON to disk. You can inspect intermediate results at any point through the web interface.

## Web Interface

The frontend is both a control panel and a monitoring dashboard:

- **Create engagements** — configure target, scope, credentials, and pipeline parameters
- **Watch progress live** — pipeline visualization (like GitHub Actions) with real-time agent streaming via WebSocket
- **Browse results** — click into any pipeline stage to inspect its output
- **Review and act** — approve re-hunt targets suggested by the Bug Chainer to kick off follow-up runs
- **Track everything** — multiple engagements, multiple runs per engagement, cumulative findings across runs

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude`)
- Optional: [Codex CLI](https://github.com/openai/codex) (`codex`)

### Installation

```bash
git clone <repo-url>
cd bug-hunting-workflow

# Install backend dependencies
pip install -r requirements.txt

# Install frontend dependencies
cd frontend && npm install && cd ..

# Start the application
python -m bug_hunter.main
```

The server starts on `http://0.0.0.0:80`. On first launch, it generates a secure random password and prints it to the console. Use it to log in.

For black box pentesting, additional tools are auto-installed on first run: subfinder, amass, httpx, nmap, ffuf, sqlmap, and playwright.

### Running a Source Code Audit

1. Open the web interface and create a new engagement
2. Select **Source Code Audit**
3. Enter a local path (`/path/to/code`) or a GitHub repo URL
4. Write your scope definition (free text — e.g., "All code in src/, focus on authentication and API endpoints, exclude test files")
5. Provide infrastructure access details (target URL, credentials — free text)
6. Click **Start**

### Running a Black Box Pentest

1. Create a new engagement
2. Select **Black Box Pentest**
3. Enter target domains (e.g., `*.example.com, api.example.com`)
4. Write your scope definition
5. Provide credentials for authenticated testing (if applicable)
6. Click **Start**

## Configuration

All pipeline parameters are configurable through the engagement creation form or via YAML:

| Parameter | Default | Description |
|---|---|---|
| `retry_limit` | 3 | Retries per subagent before logging failure |
| `subagent_timeout` | 300s | Max time per subagent before kill |
| `request_delay` | 0s | Delay between requests to target (black box) |
| `max_concurrent_infra_agents` | 5 | Parallel agents hitting live infrastructure |
| `models.*` | opus | Model selection per pipeline stage |
| `destructive_poc_policy` | cannot_validate | How to handle PoCs that would damage the target |
| `phase2_enabled` | true | Enable cross-component logic bug hunting |
| `contrived_threshold` | 3 | Max improbable preconditions before a bug is considered contrived |

See `CONTEXT.md` for the full configuration reference.

## Architecture

- **Frontend**: React with WebSocket for real-time updates
- **Backend**: FastAPI (Python) with SQLite for metadata
- **Agents**: Claude Code and Codex CLI invoked as subprocesses, each guided by a specialized markdown instruction file
- **Storage**: Structured JSON files on disk for all findings and intermediate outputs

See `CONTEXT.md` for the full technical specification, design decisions, and implementation details.
