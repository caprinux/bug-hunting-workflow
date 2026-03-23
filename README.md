# Bug Hunting Workflow

An automated security vulnerability discovery platform powered by LLM agents. It orchestrates specialized AI agents through a multi-stage pipeline to find, validate, expand, triage, and chain security bugs — then presents everything through an interactive web interface.

## What It Does

You point it at a codebase or a set of web domains. The platform deploys a team of specialized AI agents, each with a distinct role in the vulnerability discovery lifecycle:

### The Agents

**Broad Bug Hunter** — Casts the widest possible net. For source code, it maps the entire codebase, splits it into context-sized chunks, and deploys subagents in parallel to audit every file. It then reads the functionality summaries from each chunk to identify cross-component logic bugs that span multiple modules (Phase 2). Multiple models (Claude, Codex) can hunt concurrently for maximum coverage. No filtering — it flags everything suspicious.

**Scope Enumerator** *(black box only)* — Performs active and passive reconnaissance on target domains. Uses tools like subfinder, amass, httpx, and nmap to enumerate subdomains, scan ports, fingerprint technology stacks, and map endpoints. Produces a structured attack surface map that guides the Bug Hunter.

**Workload Divider** *(optional, for massive codebases)* — Splits codebases like the Linux kernel into independent subsystems so multiple Bug Hunter orchestrators can work in parallel. Identifies cross-subsystem interfaces and includes them as shared context.

**De-duplicator** *(optional)* — When multiple agents audit the same code, they flag the same bugs independently. The De-duplicator merges duplicate findings while preserving distinct bugs at different locations. Merges reasoning from all agents — multi-agent agreement is a confidence signal.

**Scope Validator** — Filters findings against the engagement's scope definition. Interprets whatever scope description is provided (bug bounty page, pentest SOW, free-form text) and makes a binary in-scope/out-of-scope decision with reasoning.

**Strict Validator** — The proving ground. Takes each suspected bug, traces the code path (source code) or analyzes the HTTP evidence (black box), writes a proof-of-concept exploit, and executes it against live infrastructure. Bugs that can't be proven go to the cannot-validate bin with a reason. Destructive PoCs (DoS, data deletion) are never executed — they're flagged as "likely exploitable, PoC destructive."

**Perfectionist** — Given a validated bug with a working PoC, pushes the exploitation primitive to its absolute maximum. SQLi read becomes SQLi write becomes RCE. SSRF becomes cloud credential theft becomes account takeover. Each escalation step is demonstrated via live PoC execution. Single-bug expansion only — no cross-bug chaining.

**Strict Triager** — The final quality gate. Aggressively questions each bug and categorizes it into three buckets: confirmed bugs (real security impact), informational findings (internal IPs, version strings — useful intelligence but not bugs), or discarded (contrived, false positive, no real impact). Fails closed — if the triager itself fails, bugs go to a review queue, never silently promoted.

**Bug Chainer** — The capstone. Takes all confirmed bugs across all runs, reads the intelligence file for context (leaked internal IPs, version strings), and constructs exploit chains that combine multiple bugs for maximum combined impact. Suggests specific re-hunt targets ("find a stored XSS to chain with this CSRF") that require human approval before executing.

### Two Modes

**Source Code Audit** — Feed it a local codebase or a GitHub repo. Multiple AI models can audit concurrently. The system splits the code into manageable chunks, scans each in parallel, then looks for cross-component logic bugs that span multiple modules.

**Black Box Pentest** — Give it target domains (including wildcards). It enumerates subdomains, maps the attack surface, then deploys agents to test each target — deciding on its own when to use tools like curl, sqlmap, ffuf, or a headless browser.

### The Pipeline

```
Source Code Audit:

  [Source Code]
       │
       ▼
  ┌──────────────────┐
  │ WORKLOAD DIVIDER │ [optional, for massive codebases]
  └────────┬─────────┘
           ▼
  ┌──────────────────┐
  │  BROAD BUG HUNTER│ Phase 1: parallel subagents on code chunks
  │  (orchestrator)  │ Phase 2: cross-component logic bugs
  └────────┬─────────┘
           ▼
  ┌──────────────────┐
  │  DE-DUPLICATOR   │ [optional, auto-enabled if multiple agents]
  └────────┬─────────┘
           ▼
  ┌──────────────────┐
  │ SCOPE VALIDATOR  │ in-scope ──→ continue
  └────────┬─────────┘ out-of-scope ──→ logged
           ▼
  ┌──────────────────┐
  │ STRICT VALIDATOR │ validated + PoC ──→ continue
  └────────┬─────────┘ cannot validate ──→ OUTPUT #1
           ▼
  ┌──────────────────┐
  │  PERFECTIONIST   │ expand single-bug primitives (per-bug)
  └────────┬─────────┘
           ▼
  ┌──────────────────┐
  │  STRICT TRIAGER  │ confirmed ──→ continue
  └────────┬─────────┘ informational ──→ OUTPUT #3
           │            discarded ──→ gone
           ▼
  ┌──────────────────┐
  │   BUG CHAINER    │ chains + re-hunt suggestions
  └────────┬─────────┘
           ▼
      [FINAL REPORT] ──→ OUTPUT #2
           │
     (human decision)
     re-hunt? ──→ back to Bug Hunter


Black Box Pentest:

  [Target Domains]
       │
       ▼
  ┌──────────────────┐
  │ SCOPE ENUMERATOR │ active + passive recon
  └────────┬─────────┘
           ▼
  ┌──────────────────┐
  │ BUG HUNTER       │ one agent per target, LLM-driven tool selection
  │ (black box)      │ checkpoint-resume for large targets
  └────────┬─────────┘
           ▼
       [shared pipeline: De-duplicator → Scope Validator → Strict Validator
        → Perfectionist → Strict Triager → Bug Chainer → Report]
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
