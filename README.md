# Bug Hunting Workflow

An automated security vulnerability discovery platform powered by LLM agents. Point it at a codebase or a set of web domains, and a team of specialized AI agents finds, validates, expands, triages, and chains security bugs — orchestrated through a multi-stage pipeline and presented in an interactive web interface with live agent streaming.

It is **fully agentic** — no Semgrep rules or CodeQL queries drive the hunt (though Semgrep is available as one input). Agents reason about code and live targets directly, which surfaces logic bugs that pattern matchers miss and produces findings that come with reasoning, working PoCs, and demonstrated impact.

> ⚠️ **Authorized use only.** This is an offensive security tool that executes proof-of-concept exploits against live infrastructure. Only run it against code and targets you own or are explicitly authorized to test.

---

## Highlights

- **Two engagement types** — audit local/GitHub source code, or black-box pentest live web domains. Both share the same downstream validation and analysis pipeline.
- **Multi-agent hunting** — Claude and Codex (GPT) hunt concurrently; multi-agent agreement becomes a confidence signal during de-duplication.
- **PoC-driven validation** — every confirmed bug ships with a proof-of-concept that was actually executed. Destructive PoCs are never run — they're flagged for human review.
- **Impact maximization** — the Perfectionist escalates single bugs to their ceiling (SQLi read → write → RCE); the Bug Chainer combines bugs into multi-step exploit chains.
- **Live web dashboard** — GitHub-Actions-style pipeline visualization, real-time agent reasoning over WebSocket, stage-by-stage output browsing, and re-hunt approval.
- **In-engagement chat** — a Claude session scoped to each engagement's findings and workspace, for ad-hoc questions and manual exploration.
- **Bug-bounty platform integration** — import program scope directly (e.g. YesWeHack) instead of writing it by hand.
- **Resumable & inspectable** — pipeline state is checkpointed; runs can be paused, resumed, and cancelled. Every stage writes structured JSON you can open at any time.

---

## How It Works

You create an **engagement** (one target) and run a **pipeline** against it. An engagement can hold many runs — an initial full run, then targeted *re-hunts* and *revalidations* — and findings accumulate across them.

### The Pipeline

```
  [ Source code / GitHub repo ]            [ Target domains / wildcards ]
                 │                                       │
                 └───────────────────┬───────────────────┘
                                     ▼
                          ┌─────────────────────┐
                          │       SETUP         │  tool checks, git clone, env prep
                          └──────────┬──────────┘
                                     ▼
                          ┌─────────────────────┐
                          │       SCOPER        │  map architecture / recon attack surface
                          └──────────┬──────────┘     (optional)
                                     ▼
              source code ──▶ ┌─────────────────────┐
                              │   SKILLS HUNTER     │  Semgrep + insecure-defaults + supply-chain
                              └──────────┬──────────┘
                                     ▼
                          ┌─────────────────────┐
                          │     BUG HUNTER      │  free-form hunting, Claude + Codex in parallel
                          │                     │  iterative — re-hunt to continue
                          └──────────┬──────────┘
                                     ▼
              source code ──▶ ┌─────────────────────┐
                              │   VARIANT HUNTER    │  find more instances of each found pattern
                              └──────────┬──────────┘
                                     ▼
                          ┌─────────────────────┐
                          │   DE-DUPLICATOR     │  merge cross-agent duplicates
                          └──────────┬──────────┘     (auto when multiple agents hunt)
                                     ▼
                          ┌─────────────────────┐
                          │   SCOPE VALIDATOR   │  drop findings that strictly violate scope
                          └──────────┬──────────┘
                                     ▼
                          ┌─────────────────────┐
                          │  STRICT VALIDATOR   │  write + execute PoCs
                          └──────────┬──────────┘  cannot prove ──▶ cannot-validate bin
                                     ▼
                          ┌─────────────────────┐
                          │   PERFECTIONIST     │  escalate each bug to maximum impact
                          └──────────┬──────────┘     (optional)
                                     ▼
                          ┌─────────────────────┐
                          │   STRICT TRIAGER    │  tag strong / weak / informational + severity
                          └──────────┬──────────┘     informational ──▶ intelligence file
                                     ▼
                          ┌─────────────────────┐
                          │    BUG CHAINER      │  combined-impact chains + re-hunt suggestions
                          └──────────┬──────────┘     (optional)
                                     ▼
                              [ FINAL REPORT ]
                                     │
                          (human approves re-hunt? ──▶ Bug Hunter continues)
```

Black-box engagements run the same pipeline but skip Skills Hunter and Variant Hunter (which are source-code-specific). A separate **revalidation** pipeline (`Testing Setup → Strict Validator → Strict Triager`) re-proves existing bugs against a freshly provisioned test environment.

### The Agents

Each stage is driven by a markdown instruction file under `agents/`. The orchestrator supplies the specific work item (a code chunk, a target, a bug to validate) as the task prompt.

| Agent | Role | Default |
|---|---|---|
| **Scoper** | Maps architecture and attack surface (source) or performs recon — passive (crt.sh, DNS, WHOIS) and active (subfinder, nmap, httpx, katana, nuclei) — for black box. | off |
| **Skills Hunter** *(source)* | Automated scans: Semgrep rulesets, insecure defaults, supply-chain/secrets audit. | on |
| **Bug Hunter** | The core agent. Hunts freely — reads code, traces data flows, tests endpoints — documenting each bug with root cause, impact, and a PoC. Runs Claude + Codex concurrently and is iterative across runs. | on |
| **Variant Hunter** *(source)* | Decomposes each found bug's root cause and greps for other instances of the same pattern elsewhere in the code. | on |
| **De-duplicator** | Merges duplicate findings from concurrent agents while preserving genuinely distinct bugs; records which agents agreed. | auto |
| **Scope Validator** | Fast pass that removes only findings that *strictly* violate the scope/rules. Does not re-judge validity or severity. | on |
| **Strict Validator** | Traces exploitability, writes a PoC (Python by default), and executes it against live infrastructure. Destructive PoCs are flagged, never run. | on |
| **Perfectionist** | Pushes a single bug's primitive to its ceiling via live PoCs (SQLi read → write → RCE; SSRF → cloud creds → ATO). Single-bug only. | off |
| **Strict Triager** | Quality gate. Tags each bug `strong` / `weak` / `informational` and assigns severity. Informational findings feed the intelligence file. | on |
| **Bug Chainer** | Cross-bug analysis. Combines confirmed bugs into chains for maximum impact and proposes targeted re-hunts (which require human approval). | off |
| **Testing Setup** *(revalidation)* | Provisions a local test environment (Docker / compose) so the Validator can re-prove bugs on localhost. | — |

Agent files live in `agents/source_code/`, `agents/black_box/`, and `agents/shared/`.

### Outputs

Three outputs accumulate per engagement (cumulative across all runs), plus a generated report:

1. **Final report** (`report.md`, `final_report.json`) — confirmed bugs with PoCs, escalated primitives, demonstrated and proposed chains, and re-hunt suggestions.
2. **Cannot-validate bin** (`all_cannot_validate.json`) — bugs that couldn't be proven, each with a reason. Includes "likely exploitable but PoC destructive." For human review — it does not feed back into the pipeline.
3. **Intelligence file** (`intelligence.json`) — informational findings (internal IPs, versions, architecture details). Feeds the Bug Chainer as ammunition for chain construction.

Every stage also writes its raw structured output under the run directory (see [Storage layout](#storage-layout)).

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- **Node.js 18+** (to build the frontend)
- **[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)** (`claude`) and the `claude_agent_sdk` Python package — required.
- **[Codex CLI](https://github.com/openai/codex)** (`codex`) and the [`codex-agent-sdk`](https://github.com/caprinux/codex-agent-sdk-python) Python package — optional, enables the second hunting agent.
- For black-box engagements: `subfinder`, `httpx`, `nmap`, `katana`, `gau`, `nuclei`, `ffuf`, `sqlmap` (and `go` to install the Go-based ones). These are **auto-installed on first run** when `auto_install_tools` is enabled.

### Install & run

```bash
git clone <repo-url>
cd bug-hunting-workflow

# Backend (includes the Claude + Codex agent SDKs)
pip install -r requirements.txt

# Frontend — build the production bundle
cd frontend && npm install && npm run build && cd ..

# Start the server
python -m bug_hunter.main
```

The backend serves the prebuilt bundle from `frontend/dist/`. After editing anything under `frontend/src/`, re-run `npm run build` (or `npm run dev` for live reload) — `npm install` alone does not rebuild.

The server listens on `http://0.0.0.0:80` by default. On first launch it generates a secure random password, saves it to a `.credentials` file (mode `0600`), and prints it to the console — use it to log in.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `BHW_HOST` | `0.0.0.0` | Bind address |
| `BHW_PORT` | `80` | Port |
| `BHW_PASSWORD` | — | Override the login password (else `config.yaml` → auto-generated) |
| `BHW_CORS_ORIGINS` | localhost + `:5173` | Comma-separated allowed CORS origins |

### Run a source code audit

1. Open the web interface and create a new engagement.
2. Select **Source Code Audit**.
3. Enter a local path (`/path/to/code`) or a GitHub repo URL (a branch/tag/commit is supported; git credentials must already be configured on the host).
4. Write your scope definition in free text (e.g. *"All code in `src/`, focus on auth and API endpoints, exclude tests"*) — or import it from a bug-bounty platform.
5. Provide infrastructure access (target URL, credentials) as free text, used later for live PoC execution.
6. Click **Start**.

### Run a black-box pentest

1. Create a new engagement and select **Black Box Pentest**.
2. Enter target domains, including wildcards (e.g. `*.example.com, api.example.com`).
3. Write your scope definition and provide credentials for authenticated testing if applicable.
4. Click **Start**.

---

## Web Interface

A React + Vite single-page app that is both control panel and monitoring dashboard:

- **Dashboard** — all engagements with status and bug-severity breakdowns.
- **Create engagement** — configure type, source/target, scope, credentials, and advanced pipeline parameters; import scope from a connected platform.
- **Run detail** — GitHub-Actions-style pipeline graph, per-stage status and counts, and an expandable panel that **streams agent reasoning live** over WebSocket. Pause, resume, or cancel a run from here.
- **Stage output browser** — click into any completed stage to inspect its JSON output, PoC files, and raw agent stream.
- **Bug / Chain / Intel browsers** — cumulative findings, exploit chains, and the intelligence file across all runs.
- **Report** — the rendered markdown report, generated on demand.
- **Chat** — a Claude session scoped to the engagement's findings and a shared workspace.
- **Platforms** — connect a bug-bounty platform (e.g. YesWeHack), scrape programs, and import scope.
- **Usage** — Claude and Codex CLI usage/cost stats.
- **Settings** — edit the global configuration.

Browser notifications fire when a run completes or a stage fails.

---

## Configuration

Pipeline behavior is driven by `config.yaml` (and per-engagement overrides set in the UI at creation time). The most useful knobs:

| Parameter | Default | Description |
|---|---|---|
| `pipeline.retry_limit` | `3` | Retries per subagent before logging failure |
| `pipeline.subagent_timeout` | `100000` | Max seconds per subagent before kill |
| `pipeline.resume` | `true` | Checkpoint state and resume after a crash |
| `pipeline.auto_install_tools` | `true` | Auto-install missing black-box tools at startup |
| `pipeline.request_delay` | `0` | Delay (s) between requests to live targets |
| `pipeline.max_concurrent_infra_agents` | `5` | Parallel agents allowed to hit live infrastructure |
| `pipeline.codex_reasoning_effort` | `xhigh` | Codex reasoning effort (`minimal`…`xhigh`) |
| `bug_hunter.agents` | `[claude, codex]` | Models hunting concurrently |
| `bug_hunter.iterations` | `1` | Bug Hunter passes per run |
| `bug_hunter.mode` | `parallel` | Run hunting agents in `parallel` or `sequential` |
| `strict_validator.destructive_poc_policy` | `cannot_validate` | How to handle PoCs that would damage the target |
| `strict_triager.contrived_threshold` | `3` | Max improbable preconditions before a bug is "contrived" |
| `scoper.enabled` / `perfectionist.enabled` / `bug_chainer.enabled` | `false` | Toggle optional stages |
| `skills_hunter.enabled` / `variant_hunter.enabled` | `true` | Toggle source-code scan stages |
| `models.*` | `gpt-5.5` / `claude-opus-4-6` | Model selection per stage |
| `auth.password` | *(empty)* | Login password; auto-generated if empty |

See [`config.yaml`](config.yaml) for the full default file and [`CONTEXT.md`](CONTEXT.md) for the complete reference and rationale.

---

## Architecture

```
Frontend (React + Vite) ──REST + WebSocket──▶ Backend (FastAPI + SQLite) ──▶ Agents (Claude / Codex SDK)
```

- **Frontend** — React 18 + Vite SPA. REST for CRUD, native WebSocket for live pipeline/agent events and chat streaming. Dark/light theme.
- **Backend** — FastAPI + Uvicorn. REST API for engagements, runs, bugs, chains, reports, platforms, and chat; a `/ws` WebSocket for real-time updates; and a pipeline **orchestrator** that runs stages sequentially with retry, pause/resume/cancel, and disk-checkpointed resumability. Auth is a signed (HMAC) bearer token or HTTP Basic.
- **Agents** — Claude and Codex invoked via their Python SDKs (`claude_agent_sdk`, `codex_agent_sdk`), each guided by a markdown instruction file and constrained to a JSON Schema from `schemas/`. Reasoning and tool use are streamed to the frontend and recorded to `stream.jsonl`.
- **Storage** — SQLite (`audit_output/db.sqlite`) for engagement/run/bug/chain/event/chat metadata; structured JSON files on disk for every finding and intermediate output.

### Bug schema

A bug is one JSON object that is progressively enriched as it flows through the pipeline — the Bug Hunter creates it (`id`, location, `vuln_class`, `description`, `reasoning`, `confidence`); the Validator adds `poc`; the Perfectionist adds `expanded_primitives`; the Triager adds `severity`; the Bug Chainer adds `chains`. Source-code bugs carry `source_file`/`line_range`; black-box bugs carry `url`/`http_evidence`. The full schema lives in [`schemas/bug_finding.json`](schemas/bug_finding.json) and is documented in [`CONTEXT.md`](CONTEXT.md).

---

## Storage layout

```
audit_output/
├── db.sqlite                              # engagement/run/bug/chain/event/chat metadata
└── engagements/<engagement_id>/
    ├── config.yaml                        # frozen engagement config
    ├── runs/<run_id>/
    │   ├── pipeline_state.json            # resumability checkpoint
    │   ├── 00_setup/ … 10_bug_chainer/    # per-stage JSON outputs, PoCs, stream.jsonl
    └── cumulative/
        ├── all_confirmed_bugs.json
        ├── all_cannot_validate.json
        ├── intelligence.json
        ├── chains.json
        ├── final_report.json
        └── report.md
```

---

## Repository layout

```
bug_hunter/            # Python backend
├── main.py            # entry point (server, auth, run recovery, static serving)
├── api/               # FastAPI routes, WebSocket, chat, platform endpoints
├── core/              # config, database, auth, events, models, cli_wrapper (agent SDK dispatch)
├── pipeline/          # orchestrator + stages/ (one class per pipeline stage)
├── platforms/         # bug-bounty platform integrations (base, registry, yeswehack)
└── utils/             # result parsing, schema validation, source acquisition, tool checks
agents/                # markdown instruction files: source_code/, black_box/, shared/
schemas/               # JSON Schemas enforcing structured agent output
frontend/              # React + Vite SPA (src/, build output in dist/)
tools/cookie_fetcher/  # helper for authenticated-session cookie capture
config.yaml            # default pipeline configuration
CONTEXT.md             # full design spec and rationale
ROADMAP.md             # planned work, tiered by priority
```

---

## Further reading

- [`CONTEXT.md`](CONTEXT.md) — complete design specification, agent methodology, and design-decision rationale.
- [`ROADMAP.md`](ROADMAP.md) — planned features and hardening work, prioritized by tier.
