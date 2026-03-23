# Bug Hunting Workflow — Project Context

## Purpose

An automated, LLM-driven bug hunting pipeline that orchestrates multiple specialized AI agents to find, validate, expand, triage, and chain security vulnerabilities. The system supports two engagement types — **source code auditing** and **black-box web pentesting** — with a shared downstream validation and analysis pipeline. It is designed to be modular, configurable, resumable, and fully inspectable at every stage.

The system is operated through a web frontend that serves as both a monitoring dashboard and control surface. It manages multiple engagements, each containing sequential pipeline runs, with real-time visibility into agent progress.

---

## System Architecture

```
Frontend (React) ←→ Backend (FastAPI + SQLite) ←→ CLI Agents (Claude Code / Codex CLI subprocesses)
```

- **Frontend**: React app with WebSocket client. Provides engagement management, pipeline visualization (GitHub Actions style), real-time progress streaming, stage output browsing, and control actions (start runs, configure, approve re-hunts). Listens on `0.0.0.0:80`. Basic HTTP auth with a secure random password. Dark/light theme toggle.
- **Backend**: FastAPI server. REST API for engagements, runs, and findings. WebSocket server for real-time updates. Pipeline orchestrator that spawns and manages CLI subprocesses. SQLite for metadata persistence. Output files on disk for full intermediate visibility.
- **CLI Agent Layer**: Claude Code and Codex CLI invoked as non-interactive subprocesses. Each agent is defined by a markdown instruction file. The orchestrator passes dynamic context (bug findings, summaries, infra config) as the task prompt. Structured output is captured via `--output-format json` (Claude) or `--json` (Codex).

---

## Engagement Model

An **engagement** is a logical container for auditing a single target. It contains one or more **runs** (pipeline executions). Configuration is locked at engagement creation and cannot be modified between runs.

```
Engagement
├── type: "source_code" | "black_box"
├── config (locked at creation):
│   ├── source path or git repo URL (source_code)
│   ├── target domains/wildcards (black_box)
│   ├── scope definition (unstructured text)
│   ├── infra config (unstructured text — URLs, creds, auth details)
│   └── pipeline parameters (full YAML config)
├── runs[]:
│   ├── Run 1: initial full pipeline
│   ├── Run 2: re-hunt (targeted, e.g., "find XSS in auth module")
│   └── Run N: further re-hunts
├── cumulative state:
│   ├── all confirmed bugs (grows across runs)
│   ├── intelligence file (grows across runs)
│   ├── cannot-validate bin (grows across runs)
│   └── chains (updated each run)
└── cost total (USD, tracked from CLI output)
```

Sequential runs within an engagement share cumulative context. The Bug Chainer sees all confirmed bugs across all prior runs when chaining.

---

## Pipeline Design

### Design Principles

1. **Modularity**: Each stage is independently runnable with the right input. The pipeline orchestrator strings them together, but no stage assumes it was called by the orchestrator. External tool output (e.g., from Semgrep or manual review) can be injected at any stage.
2. **Verbose intermediate output**: Every stage writes structured JSON output to disk. The frontend allows clicking into any completed stage to browse its outputs. The orchestrator streams agent reasoning in real-time via WebSocket.
3. **Retry logic**: On any failure, the orchestrator retries up to 3 times. After 3 failures, the finding goes to the cannot-validate bin and the pipeline continues. Human intervention happens at the end of the full workflow, not mid-pipeline.
4. **Retry idempotency**: For agents that hit live infrastructure, the orchestrator distinguishes pre-execution failures (safe to retry) from post-execution failures (log and move to cannot-validate immediately, no retry) and ambiguous failures (retry with caution, log each attempt).
5. **Resumability**: Pipeline state is checkpointed to disk. If the pipeline crashes, it resumes from where it left off by checking which items have been processed at each stage. Each bug has a stable `id` assigned by the Bug Hunter for tracking.
6. **Configurable parameters**: Nearly every aspect of the pipeline is configurable via YAML — concurrency limits, timeouts, model selection per stage, tool toggles, thresholds, etc.
7. **No static analysis tools**: The pipeline is fully agentic. It does not use Semgrep, CodeQL, or other static analysis tools. All bug finding is done by LLM agents reasoning about code or interacting with targets.

### Shared Pipeline (both engagement types)

Both engagement types produce bug findings in a standardized schema. After the type-specific Bug Hunter stage, findings converge into this shared pipeline:

```
Bug Findings → De-duplicator [optional] → Scope Validator → Strict Validator → Perfectionist → Strict Triager → Bug Chainer → Outputs
```

### Source Code Audit Pipeline

```
Source Code
  → Workload Divider [optional, for massive codebases]
  → Broad Bug Hunter Orchestrator
    → Phase 1: Parallel broad sweep (N subagents on code chunks)
    → Phase 2: Cross-component logic bug hunting (targeted subagents)
  → [shared pipeline]
```

### Black Box Pentest Pipeline

```
Target Domains
  → Scope Enumeration Agent (active + passive recon)
  → Bug Hunter (one per target, with checkpoint-resume)
  → [shared pipeline]
```

---

## Agent Descriptions

### Source Code Agents

#### Workload Divider (`agents/source_code/workload_divider.md`)
- **Purpose**: Splits massive codebases (e.g., Linux kernel) into independent subsystems so each can be assigned its own Bug Hunter Orchestrator.
- **When used**: Optional. Enabled for codebases too large for a single Bug Hunter Orchestrator to map.
- **Process**: Analyzes top-level structure, identifies independent subsystems, identifies cross-subsystem interfaces (included as shared context for each orchestrator).
- **Output**: List of subsystem assignments, each with its code paths and shared interface context.

#### Bug Hunter Orchestrator (`agents/source_code/bug_hunter_orchestrator.md`)
- **Purpose**: Maps a codebase (or subsystem), splits it into context-sized chunks, deploys subagents for broad bug finding, then deploys targeted subagents for cross-component logic bugs.
- **Process**:
  1. **Setup**: Map directory structure. Identify shared/common code. Run a dedicated subagent on shared code to get its functionality summary. Bin-pack remaining modules into chunks that fit within the subagent context budget, respecting semantic boundaries (packages, modules, directories). If a single module exceeds the budget, subdivide further. Group small modules together.
  2. **Phase 1 — Parallel Broad Sweep**: Deploy N subagents concurrently. Each receives its code chunk + the shared code summary. Each produces structured bug findings + a security-focused functionality summary.
  3. **Phase 2 — Cross-Component Logic Bugs**: Read all functionality summaries. Identify suspicious cross-module interactions (e.g., Module A trusts a header that Module B lets users control). Deploy targeted subagents, each given specific files from multiple modules + the interaction hypothesis + relevant summaries. Single round — no iteration.
- **Output**: All bug findings (Phase 1 + Phase 2 merged). All functionality summaries (passed downstream to Validator, Perfectionist, Triager, Bug Chainer).

#### Bug Hunter Subagent (`agents/source_code/bug_hunter_subagent.md`)
- **Purpose**: Audit a chunk of source code for all potential vulnerabilities.
- **Input**: Code chunk (files/modules), shared code summary.
- **Behavior**: No filtering — flags everything suspicious. Maximizes coverage. Does not prioritize or rank. Open-ended (not limited to a vulnerability taxonomy).
- **Output**:
  - Structured bug findings (standard schema: id, source_file, line_range, vuln_class, description, reasoning, confidence).
  - Security-focused functionality summary: what inputs the module accepts and from where, what security-relevant operations it performs, what assumptions it makes about its inputs, what it outputs and to where, what authentication/authorization is enforced.

#### Bug Hunter Logic Subagent (`agents/source_code/bug_hunter_logic_subagent.md`)
- **Purpose**: Investigate a specific cross-component interaction hypothesis for logic bugs.
- **Input**: Specific files from multiple modules, the interaction hypothesis, relevant functionality summaries.
- **Output**: Structured bug findings if the hypothesis yields a vulnerability, or a null result if it doesn't.

#### Strict Validator — Source Code (`agents/source_code/strict_validator.md`)
- **Purpose**: Prove or disprove exploitability of a suspected bug through static code tracing and live PoC execution.
- **Input**: A bug finding (standard schema), infrastructure access instructions (unstructured text), functionality summaries.
- **Process**: Trace the data flow statically through the codebase. Understand how user input reaches the vulnerable sink, what sanitization/validation exists in the path, what middleware or framework behavior might prevent exploitation. Write a PoC (default: Python). Execute the PoC against the live infrastructure. Observe the result.
- **Destructive PoC policy**: If the PoC would be destructive (DoS, data deletion, resource exhaustion), route to the cannot-validate bin with a note: "likely exploitable, PoC destructive." Do not execute.
- **Execution failure handling**: Distinguish "bug not real" (→ cannot-validate) from "infra issue" (→ retry). Post-execution failures (PoC sent but response parsing failed) are not retried — logged immediately.
- **Output**: Two files:
  - Validated bugs with working PoC (continues pipeline).
  - Cannot-validate bugs with reasons (OUTPUT #1). Includes destructive-but-likely-exploitable findings.

#### Perfectionist — Source Code (`agents/source_code/perfectionist.md`)
- **Purpose**: Given a single validated bug, expand its primitive to the maximum possible impact through live demonstration.
- **Input**: A validated bug with PoC, infrastructure access, functionality summaries.
- **Scope**: Single-bug expansion ONLY. Does not look at other bugs. Does not suggest cross-bug chains. Answers: "what is the absolute maximum an attacker can achieve with this one bug alone?"
- **Examples**: SQLi read → SQLi write → RCE via INTO OUTFILE. SSRF → metadata endpoint → cloud credentials → account takeover. Arbitrary file read → private key → forge auth token.
- **Method**: Live PoC execution against infrastructure for each expansion step.
- **Output**:
  - Demonstrated expansions (proven via executed PoC).
  - Theoretical expansions (couldn't test in this environment, clearly labeled with reason).

### Black Box Agents

#### Scope Enumeration Agent (`agents/black_box/scope_enumerator.md`)
- **Purpose**: Enumerate all targets within scope, map the complete attack surface.
- **Input**: Target domains/wildcards, scope definition.
- **Recon modes** (default: both):
  - Passive: certificate transparency logs, DNS records, Wayback Machine, WHOIS, search engine dorking.
  - Active: subdomain brute-force, port scanning, HTTP crawling, tech fingerprinting.
- **Tools**: subfinder, amass, httpx, nmap, and others. Runs tools that write to files, then parses the output. The LLM orchestrates tool usage but the heavy lifting is in the tools.
- **Checkpoint-resume**: If the scope is massive, uses the same checkpoint mechanism as the black-box Bug Hunter.
- **Output**:
  - Expanded target list (all live subdomains/IPs).
  - Per-target detail: open ports, tech stack, discovered endpoints, parameters, authentication mechanisms.
  - Attack surface map (passed downstream — equivalent of functionality summaries for source code).

#### Bug Hunter — Black Box (`agents/black_box/bug_hunter.md`)
- **Purpose**: Test a single target (subdomain/API group) for vulnerabilities through active interaction.
- **Input**: Target assignment (from scope enumeration output), infra config (credentials, auth details), attack surface map for context.
- **Behavior**: LLM-driven tool selection. The agent decides when to use curl, playwright/selenium, sqlmap, ffuf, custom Python scripts, or any other available tool. Tests all user roles if credentials are provided. If an authentication barrier cannot be overcome programmatically (MFA, CAPTCHA, complex OAuth), flags it for human intervention.
- **Checkpoint-resume (hybrid Option C)**:
  - Agent writes a structured progress file after every meaningful unit of work (completing an endpoint test, finishing a test category).
  - Orchestrator monitors context usage via `stream-json` output as a safety net.
  - On checkpoint: orchestrator terminates the subprocess, spawns a new instance with the progress file.
  - Progress file captures: tested endpoints/parameters/flows (with results), findings so far (standard schema), observations (patterns, auth mechanisms, tech details — the agent's "intuition"), remaining work areas, active hypotheses being pursued.
  - The new instance reads the progress file and continues from where the previous instance left off.
- **Output**: Structured bug findings (standard schema) with HTTP evidence (request/response pairs that demonstrate the issue). Findings include partial PoC from interaction evidence.

#### Strict Validator — Black Box (`agents/black_box/strict_validator.md`)
- **Purpose**: Verify that a black-box finding is genuinely exploitable by reproducing it with a clean PoC.
- **Input**: A bug finding with HTTP evidence from the Bug Hunter, infrastructure access, attack surface map.
- **Process**: Analyze the Bug Hunter's evidence (request/response). Reproduce the triggering request. Verify the response confirms exploitation. Write a clean, standalone PoC. The Bug Hunter's evidence gives this agent a head start — it's re-validation and cleanup, not validation from scratch.
- **Same destructive PoC policy and failure handling as source code variant.**
- **Output**: Same two-file structure as source code variant.

#### Perfectionist — Black Box (`agents/black_box/perfectionist.md`)
- **Purpose**: Same as source code variant — expand a single bug's primitive to maximum impact.
- **Difference**: Expands via blind probing against the live target rather than source code analysis. Tries different payloads, internal targets, escalation paths.
- **Input**: Validated bug with PoC, infrastructure access, attack surface map.
- **Output**: Same structure as source code variant.

### Shared Agents

#### De-duplicator (`agents/shared/deduplicator.md`)
- **Purpose**: Merge duplicate findings when multiple agents (Claude + Codex, or multiple Bug Hunter instances) flag the same underlying issue.
- **When used**: Optional. Auto-enabled when multiple agents ran the Bug Hunter stage.
- **Deduplication logic**:
  - Same file and line (source code) or same URL and parameter (black box) → obvious duplicate, merge.
  - Overlapping line range or same endpoint with similar payload → likely duplicate, merge.
  - Different paths to the same vulnerable sink → semantic duplicate, merge.
  - Same vulnerability pattern at genuinely different locations → NOT duplicates, preserve as distinct bugs.
- **Merge behavior**: Combine reasoning from all agents that flagged the finding. Note which agents agreed (multi-agent agreement = higher confidence signal).
- **Output**: De-duplicated findings list + duplicate group log (which findings were merged).

#### Scope Validator (`agents/shared/scope_validator.md`)
- **Purpose**: Filter findings against the engagement's scope definition.
- **Input**: Bug finding, unstructured scope definition text.
- **Behavior**: Interprets whatever scope description is provided — could be a bug bounty scope page, a pentest SOW excerpt, or free-form text. Makes a binary decision (in-scope / out-of-scope) with reasoning.
- **Output**: In-scope findings (continue pipeline) and out-of-scope findings (logged with reasons, not processed further).

#### Strict Triager (`agents/shared/strict_triager.md`)
- **Purpose**: Final quality gate on individual bugs. Evaluates the bug plus its expanded impact from the Perfectionist. Removes noise.
- **Three output categories**:
  1. **Confirmed bugs**: Real vulnerabilities with demonstrated security impact. Continue to Bug Chainer.
  2. **Informational findings**: True and factual but no direct security impact. Non-sensitive information like internal IPs, version strings, stack traces, architecture details. Logged to intelligence file. Available to Bug Chainer as context. Not a bug but valuable for infrastructure mapping and chain reasoning.
  3. **Discarded**: Findings where the security impact is contrived (requires 3+ improbable preconditions an attacker cannot control), not real, or not meaningful even as intelligence.
- **Key distinctions**:
  - IDOR exposing sensitive data (PII, private messages, credentials) = confirmed bug, not informational.
  - Leaking internal IPs, software versions = informational (useful intelligence, not a bug).
  - The Triager does NOT filter theoretical chain suggestions from the Perfectionist — those are for the Bug Chainer.
  - The Triager evaluates whether the Perfectionist's demonstrated expansions are realistic.

#### Bug Chainer (`agents/shared/bug_chainer.md`)
- **Purpose**: Cross-bug analysis. Takes the full set of confirmed bugs and chains their primitives together for maximum combined impact.
- **Input**: All confirmed bugs (with PoCs and expanded primitives) from current and prior runs. Intelligence file (informational findings — internal IPs, version info, etc., used as context). Functionality summaries or attack surface map.
- **Behavior**: Identifies compatible primitives across different bugs. Reasons about execution order and state dependencies (does step N set up preconditions for step N+1?). Attempts to write combined PoCs for demonstrated chains. Proposes untested chains (clearly labeled).
- **Re-hunt suggestions**: Identifies specific bug classes that would enable higher-impact chains (e.g., "a stored XSS in the admin panel would chain with confirmed CSRF #4 for full account takeover"). These require human approval before the Bug Hunter is re-deployed.
- **Output**:
  - Individual confirmed bugs with PoCs.
  - Demonstrated chains with combined PoCs.
  - Proposed chains (untested, labeled).
  - Re-hunt suggestions (targets for follow-up runs).

---

## Pipeline Outputs

Three outputs per engagement (cumulative across runs):

1. **Cannot-validate bin** (`cumulative/all_cannot_validate.json`): Bugs that couldn't be proven exploitable. Includes reasons for each. Includes "likely exploitable but PoC destructive" findings. This is a final output — it does not feed back into the pipeline. Reviewed by humans at the end.
2. **Final report** (`cumulative/final_report.json`): All confirmed bugs with PoCs and expanded primitives. Demonstrated chains with combined PoCs. Proposed chains (untested). Re-hunt suggestions. Displayed through the frontend.
3. **Intelligence file** (`cumulative/intelligence.json`): All informational findings. Non-sensitive information useful for infrastructure mapping. Available to the Bug Chainer as context for cross-bug reasoning. Grows across runs.

---

## Bug Schema

Every bug flows through the pipeline as a single JSON object that gets progressively enriched by each stage. Fields are added by the stage indicated.

```json
{
  "id": "bug-001",
  "found_by": ["claude-opus", "codex-o3"],

  "source_file": "src/auth/login.py",
  "line_range": "45-62",

  "url": "https://target.com/api/login",
  "http_evidence": {
    "request": "POST /api/login HTTP/1.1\n...",
    "response": "HTTP/1.1 200 OK\n..."
  },

  "vuln_class": "CWE-89",
  "vuln_type": "SQL Injection",
  "description": "User-controlled input in username parameter passed directly to SQL query without parameterization",
  "reasoning": "The username parameter from the POST body is concatenated into an SQL query string at line 52...",
  "confidence": "high",

  "poc": {
    "language": "python",
    "file": "pocs/bug_001_poc.py",
    "execution_result": "success",
    "output": "Retrieved admin password hash: $2b$12$..."
  },

  "expanded_primitives": {
    "demonstrated": [
      {
        "primitive": "SQLi read -> SQLi write via UNION + INTO OUTFILE",
        "poc_file": "expanded_pocs/bug_001_write.py",
        "execution_result": "success"
      }
    ],
    "theoretical": [
      {
        "primitive": "SQLi write -> RCE via webshell upload",
        "reason_not_demonstrated": "Web root not writable in test environment"
      }
    ]
  },

  "severity": "critical",
  "triager_notes": "Confirmed critical: demonstrated write primitive enables data manipulation. Theoretical RCE path blocked only by environment config, likely exploitable in production.",

  "chains": ["chain-001"]
}
```

Field presence varies by engagement type:
- Source code: `source_file`, `line_range` present. `url`, `http_evidence` absent.
- Black box: `url`, `http_evidence` present. `source_file`, `line_range` absent.

Fields populated by stage:
- **Bug Hunter**: `id`, `found_by`, `source_file`/`line_range` or `url`/`http_evidence`, `vuln_class`, `vuln_type`, `description`, `reasoning`, `confidence`.
- **Strict Validator**: `poc`.
- **Perfectionist**: `expanded_primitives`.
- **Strict Triager**: `severity`, `triager_notes`.
- **Bug Chainer**: `chains`.

---

## Configuration

All parameters configurable via YAML. Defaults shown.

```yaml
pipeline:
  output_dir: "./audit_output"
  verbose: true
  retry_limit: 3
  subagent_timeout: 300
  resume: true
  bug_schema_version: "1.0"
  request_delay: 0                      # seconds between requests to target
  max_concurrent_infra_agents: 5

engagement:
  type: "source_code"                   # or "black_box"
  source_path: ""                       # local directory (source_code)
  source_repo: ""                       # git URL, cloned at setup (source_code)
  target_domains: []                    # domains/wildcards (black_box)
  scope_definition: ""                  # unstructured text
  infra_config: ""                      # unstructured text

workload_divider:
  enabled: false
  subsystem_strategy: "auto"
  manual_subsystems: []

broad_bug_hunter:
  agents: ["claude"]                    # models/CLIs to use concurrently
  context_budget: 150000               # tokens per subagent chunk
  phase2_enabled: true
  shared_code_paths: []
  file_extensions: []
  exclude_paths: []

scope_enumerator:
  recon_mode: "both"                    # active | passive | both

black_box_bug_hunter:
  checkpoint_context_threshold: 0.7

deduplicator:
  enabled: false                        # auto-enabled if multiple agents
  similarity_threshold: 0.8

scope_validator: {}                     # no additional config

strict_validator:
  destructive_poc_policy: "cannot_validate"
  max_concurrent: 5
  poc_language: "python"

perfectionist:
  max_concurrent: 3

strict_triager:
  contrived_threshold: 3
  severity_floor: "low"

bug_chainer:
  max_concurrent: 2
  rehunt_auto_approve: false            # always false

models:
  workload_divider: "opus"
  bug_hunter_orchestrator: "opus"
  bug_hunter_subagent: "opus"
  scope_enumerator: "opus"
  black_box_bug_hunter: "opus"
  deduplicator: "opus"
  scope_validator: "opus"
  strict_validator: "opus"
  perfectionist: "opus"
  strict_triager: "opus"
  bug_chainer: "opus"

auth:
  password: ""                          # auto-generated if empty
```

---

## CLI Invocation Patterns

### Claude Code

```bash
IS_SANDBOX=1 claude --print \
  --output-format stream-json \
  --dangerously-skip-permissions \
  --model opus \
  --append-system-prompt-file agents/source_code/bug_hunter_subagent.md \
  --json-schema schemas/bug_findings.json \
  --no-session-persistence \
  --verbose \
  "task prompt with dynamic context"
```

- `--print`: non-interactive mode (required for subprocess use).
- `--output-format stream-json`: streams JSONL events in real-time (for WebSocket forwarding).
- `--dangerously-skip-permissions`: auto-approves all tool use.
- `--append-system-prompt-file`: loads agent instructions from markdown file.
- `--json-schema`: enforces structured output conforming to bug schema.
- `--no-session-persistence`: prevents session file clutter.
- `--verbose`: required for `stream-json`.
- Working directory set via `subprocess.run(cwd=...)` (no `--cwd` flag exists).

### Codex CLI

```bash
codex exec \
  -C /path/to/working/dir \
  --dangerously-bypass-approvals-and-sandbox \
  --json \
  --ephemeral \
  -m o3 \
  --skip-git-repo-check \
  "task prompt with dynamic context"
```

- `codex exec`: non-interactive subcommand.
- `-C`: sets working directory.
- `--dangerously-bypass-approvals-and-sandbox`: auto-approves everything.
- `--json`: JSONL output for structured capture.
- `--ephemeral`: no session persistence.
- `--skip-git-repo-check`: allows running outside git repos.

### Output Capture

Both CLIs print structured JSON to stdout. The orchestrator captures stdout, extracts the structured bug findings / results, and writes them to the appropriate output file in the run directory. The raw CLI output is also preserved for debugging.

---

## Output Directory Structure

```
audit_output/
├── engagements/
│   └── <engagement_id>/
│       ├── config.yaml                          # frozen engagement config
│       ├── engagement.json                      # metadata, cumulative state
│       │
│       ├── runs/
│       │   └── <run_id>/
│       │       ├── pipeline_state.json          # resumability checkpoint
│       │       ├── logs/
│       │       │   └── pipeline.log
│       │       │
│       │       ├── 00_setup/
│       │       │   └── setup.json               # tool check, source acquisition
│       │       │
│       │       ├── 01_workload_divider/         # source_code, if enabled
│       │       │   └── subsystems.json
│       │       │
│       │       ├── 01_scope_enumerator/         # black_box only
│       │       │   ├── subdomains.json
│       │       │   ├── ports.json
│       │       │   ├── tech_stack.json
│       │       │   ├── endpoints.json
│       │       │   └── attack_surface_map.json
│       │       │
│       │       ├── 02_bug_hunter/
│       │       │   ├── codebase_map.json        # source_code
│       │       │   ├── phase1/                  # source_code
│       │       │   │   ├── chunk_001_findings.json
│       │       │   │   ├── chunk_001_summary.json
│       │       │   │   └── ...
│       │       │   ├── phase2/                  # source_code
│       │       │   │   └── interaction_001_findings.json
│       │       │   ├── target_001/              # black_box
│       │       │   │   ├── progress.json
│       │       │   │   ├── findings.json
│       │       │   │   └── http_evidence/
│       │       │   ├── all_findings.json
│       │       │   └── all_summaries.json
│       │       │
│       │       ├── 03_deduplicator/
│       │       │   ├── deduplicated_findings.json
│       │       │   └── duplicate_groups.json
│       │       │
│       │       ├── 04_scope_validator/
│       │       │   ├── in_scope.json
│       │       │   └── out_of_scope.json
│       │       │
│       │       ├── 05_strict_validator/
│       │       │   ├── validated_bugs.json
│       │       │   ├── cannot_validate.json
│       │       │   └── pocs/
│       │       │       └── bug_<id>_poc.py
│       │       │
│       │       ├── 06_perfectionist/
│       │       │   ├── bug_<id>_expanded.json
│       │       │   └── expanded_pocs/
│       │       │       └── ...
│       │       │
│       │       ├── 07_strict_triager/
│       │       │   ├── confirmed_bugs.json
│       │       │   ├── informational.json
│       │       │   └── discarded.json
│       │       │
│       │       └── 08_bug_chainer/
│       │           ├── individual_bugs.json
│       │           ├── demonstrated_chains.json
│       │           ├── proposed_chains.json
│       │           ├── rehunt_suggestions.json
│       │           └── chain_pocs/
│       │               └── ...
│       │
│       └── cumulative/
│           ├── all_confirmed_bugs.json
│           ├── all_cannot_validate.json
│           ├── intelligence.json
│           ├── chains.json
│           └── final_report.json
│
└── db.sqlite
```

---

## Frontend Specification

### Tech Stack

React frontend with WebSocket client. Communicates with FastAPI backend via REST API + WebSocket.

### Pages

| Route | Purpose |
|---|---|
| `/login` | Basic HTTP auth |
| `/` | Dashboard: all engagements, status overview |
| `/engagements/new` | Create engagement form (type selection, source/target config, scope, infra config, advanced pipeline params) |
| `/engagements/:id` | Engagement detail: config summary, run history, cumulative findings, cost total |
| `/engagements/:id/runs/:id` | Run detail: pipeline visualization, stage output browser, real-time streaming, re-hunt approval |
| `/engagements/:id/bugs` | All confirmed bugs across runs |
| `/engagements/:id/chains` | All chains across runs |
| `/engagements/:id/intel` | Intelligence file browser |

### Pipeline Visualization

GitHub Actions style. Each stage is a node with edges showing data flow. Nodes display:
- Status: pending / running / completed / failed
- Bug count passing through
- Clickable to inspect outputs

Running nodes show live progress (e.g., "Phase 1: 8/12 subagents complete", "Validating bug 3/17"). Expandable panel shows agent reasoning streamed in real-time.

### Real-time Updates

WebSocket pushes events: stage status changes, subagent progress, bug counts, errors, completion. Browser notification (toast + native browser notification) when a run completes.

### Theme

Dark and light theme with toggle. Persisted in local storage.

### Source Code Acquisition

The "create engagement" form handles source code acquisition:
- Local directory path (validated that it exists on the server).
- GitHub repo URL (cloned via `git clone` at setup). Supports specific branch/tag/commit.
- Assumes git credentials are already configured on the machine.

---

## Tool Dependencies

### Source Code Audit
- `claude` — Claude Code CLI
- `codex` — Codex CLI (if configured as a Bug Hunter agent)
- `git` — repo cloning
- `python3` + `pip` — PoC execution

### Black Box Pentest (additional)
- `subfinder` — subdomain discovery
- `amass` — subdomain enumeration
- `httpx` — HTTP probing
- `nmap` — port scanning
- `ffuf` — web fuzzing
- `sqlmap` — SQL injection testing
- `playwright` or `selenium` — browser automation
- `curl` — HTTP requests

The pipeline auto-installs missing tools at startup based on engagement type. Results logged to `00_setup/setup.json`.

---

## Agent File Structure

```
agents/
├── shared/
│   ├── deduplicator.md
│   ├── scope_validator.md
│   ├── strict_triager.md
│   └── bug_chainer.md
│
├── source_code/
│   ├── workload_divider.md
│   ├── bug_hunter_orchestrator.md
│   ├── bug_hunter_subagent.md
│   ├── bug_hunter_logic_subagent.md
│   ├── strict_validator.md
│   └── perfectionist.md
│
└── black_box/
    ├── scope_enumerator.md
    ├── bug_hunter.md
    ├── strict_validator.md
    └── perfectionist.md
```

13 agent files total. Each is a markdown file containing the agent's role, methodology, behavioral constraints, and output format requirements. The agent file defines the role; the subprocess call provides the specific work item as the task prompt.

Agent files are loaded via `--append-system-prompt-file` (Claude Code) or included in the prompt (Codex CLI). Dynamic context (the specific bug to analyze, infra config, functionality summaries) is passed as the task prompt argument.

---

## Key Design Decisions and Rationale

### Why separate agent files per engagement type instead of one agent handling both modes?
Source code validation (static code trace → write PoC → execute) and black box validation (analyze HTTP evidence → reproduce → verify) are fundamentally different methodologies. Cramming both into one file with conditional logic bloats instructions, wastes context on irrelevant methodology, and prevents independent tuning. Agents that are genuinely identical across modes (Triager, Bug Chainer, Scope Validator, De-duplicator) remain shared.

### Why no static analysis tools (Semgrep, CodeQL)?
The pipeline is designed to be fully agentic. LLM agents reason about code directly. This avoids dependency on rule sets, produces findings that include reasoning (not just pattern matches), and can catch logic bugs that static analysis tools miss. Static analysis output can be injected at the De-duplicator stage if desired, but the pipeline does not depend on it.

### Why functionality summaries / attack surface maps are passed downstream?
These compressed representations of the target give downstream agents (Validator, Perfectionist) application context they wouldn't otherwise have. The Validator traces exploitability better when it understands the full data flow across modules. The Perfectionist expands primitives more effectively when it knows what internal services exist.

### Why the Perfectionist does not chain bugs?
Clean separation of concerns. The Perfectionist answers "what is the maximum impact of THIS ONE bug alone?" The Bug Chainer answers "what is the maximum impact of ALL confirmed bugs combined?" Mixing these creates scope creep and makes per-bug analysis dependent on the full bug set.

### Why the Triager comes after the Perfectionist, not before?
The Triager evaluates the full expanded impact, not just the base finding. A bug that looks medium-severity before expansion might be critical after the Perfectionist demonstrates RCE. Triaging before expansion would kill findings prematurely.

### Why informational findings are preserved (not discarded)?
Non-sensitive information (internal IPs, version strings, architecture details) has no direct security impact but is valuable intelligence for the Bug Chainer. A leaked internal IP from a stack trace tells the Bug Chainer where to aim a confirmed SSRF. Discarding this throws away ammunition for chain construction.

### Why the cannot-validate bin is a dead end?
Simplicity. These findings couldn't be proven and require human review. Feeding them back into the pipeline creates complexity (when would they re-enter? under what conditions?) without clear benefit. The human reviews them at the end and can manually create a new run if any deserve further investigation.

### Why the re-hunt loop requires human approval?
Prevents autonomous infinite loops. The Bug Chainer suggests specific re-hunt targets, but a human decides whether to invest the resources. The re-hunt enters the pipeline at the De-duplicator (to check against existing findings) and continues normally.

### Why checkpoint-resume for black-box Bug Hunter?
Unlike source code where file sizes are known upfront, a website's complexity is unknown until exploration begins. An endpoint might be trivial or incredibly complex. The checkpoint-resume mechanism (hybrid Option C) lets the agent adapt to unknown-size targets without exceeding context limits.

### Why SQLite?
The system runs locally on a single machine. SQLite is zero-configuration, single-file, and sufficient for the metadata workload (engagement/run tracking, not high-throughput queries). The actual findings and outputs live as JSON files on disk.

---

## Implementation Order

```
Phase 1: Foundation
  1. Project scaffolding (Python package structure, FastAPI app, React app)
  2. SQLite database schema (engagements, runs, stage results)
  3. Bug schema definition (JSON Schema file for --json-schema enforcement)
  4. Configuration system (YAML loading, validation, defaults)

Phase 2: Backend Core
  5. CLI subprocess wrapper (Claude Code + Codex invocation, output parsing,
     stream-json reading, output normalization)
  6. Pipeline orchestrator (stage sequencing, state tracking, retry logic,
     resumability, concurrency management)
  7. WebSocket server (event types, broadcasting to connected clients)
  8. REST API (CRUD for engagements and runs, output file serving,
     re-hunt approval endpoint)
  9. Tool dependency checker + auto-installer
  10. Source code acquisition (git clone handling, local path validation)

Phase 3: Agent Files
  11. Source code agents (workload_divider, bug_hunter_orchestrator,
      bug_hunter_subagent, bug_hunter_logic_subagent,
      strict_validator, perfectionist)
  12. Black box agents (scope_enumerator, bug_hunter,
      strict_validator, perfectionist)
  13. Shared agents (deduplicator, scope_validator,
      strict_triager, bug_chainer)

Phase 4: Frontend
  14. Auth + layout shell + theme toggle
  15. Dashboard + engagement list
  16. Create engagement form (with source acquisition)
  17. Pipeline visualization (nodes, edges, status indicators)
  18. Stage output browser (JSON viewer, PoC file viewer)
  19. Real-time WebSocket integration (live progress, streaming)
  20. Re-hunt approval interface
  21. Browser notifications (toast + native)
  22. Bug browser, chain browser, intelligence browser pages

Phase 5: Integration + Polish
  23. End-to-end testing (source code audit flow)
  24. End-to-end testing (black box pentest flow)
  25. Checkpoint-resume testing
  26. Multi-run engagement testing
  27. Error handling, edge cases, retry verification
```
