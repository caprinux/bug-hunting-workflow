# Bug Hunting Workflow

An automated security vulnerability discovery platform powered by LLM agents. It orchestrates specialized AI agents through a multi-stage pipeline to find, validate, expand, triage, and chain security bugs — then presents everything through an interactive web interface.

## What It Does

You point it at a codebase or a set of web domains. It deploys AI agents that:

1. **Hunt** — Systematically scan source code or probe web targets for vulnerabilities
2. **Validate** — Write and execute proof-of-concept exploits against live infrastructure to prove bugs are real
3. **Expand** — Push each confirmed bug to its maximum impact (e.g., escalating a SQL injection read into remote code execution)
4. **Triage** — Filter out noise, keeping only bugs with genuine security impact
5. **Chain** — Combine multiple bugs into higher-impact attack chains

The result is a set of confirmed vulnerabilities, each with a working PoC, expanded to maximum severity, and chained where possible.

## Two Modes

### Source Code Audit
Feed it a local codebase or a GitHub repo. Multiple AI models (Claude, Codex) can audit concurrently. The system splits the code into manageable chunks, scans each in parallel, then looks for cross-component logic bugs that span multiple modules.

### Black Box Pentest
Give it target domains (including wildcards). It enumerates subdomains, maps the attack surface, then deploys agents to test each target — deciding on its own when to use tools like curl, sqlmap, ffuf, or a headless browser.

## The Pipeline

```
Source Code Audit:
  Code → [Workload Divider] → Bug Hunter → De-duplicator → Scope Validator
    → Strict Validator → Perfectionist → Strict Triager → Bug Chainer → Report

Black Box Pentest:
  Domains → Scope Enumerator → Bug Hunter → De-duplicator → Scope Validator
    → Strict Validator → Perfectionist → Strict Triager → Bug Chainer → Report
```

Every stage writes structured output to disk. You can inspect intermediate results at any point through the web interface.

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

## Outputs

Each engagement produces three output files (cumulative across runs):

- **Confirmed bugs** — Validated vulnerabilities with working PoCs, expanded to maximum impact, chained where possible
- **Cannot-validate bin** — Bugs that couldn't be proven (inconclusive evidence, destructive PoCs, infra issues) — for human review
- **Intelligence file** — Non-sensitive informational findings (internal IPs, version strings, architecture details) useful for manual analysis

All outputs are browsable through the web interface and stored as structured JSON on disk.

## Architecture

- **Frontend**: React with WebSocket for real-time updates
- **Backend**: FastAPI (Python) with SQLite for metadata
- **Agents**: Claude Code and Codex CLI invoked as subprocesses, each guided by a specialized markdown instruction file
- **Storage**: Structured JSON files on disk for all findings and intermediate outputs

See `CONTEXT.md` for the full technical specification, design decisions, and implementation details.
