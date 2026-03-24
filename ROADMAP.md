# Roadmap

Improvements organized by priority tier. Items within each tier are roughly ordered by value.

---

## Tier 0 — Completed

All Tier 0 items have been implemented.

- **Per-agent live visibility** — agent status cards, per-agent progress events, color-coded event log
- **Persist WebSocket events** — events table in SQLite, historical event loading on page mount

---

## Tier 1 — Nice to Have (quality and robustness)

These improve reliability and debuggability without changing the core workflow.

### Replace CLI subprocess with Claude Agent SDK
Replace the `claude --print` subprocess invocation in `cli_wrapper.py` with the `claude-code-sdk` Python package. Gives native async/await, typed events, proper exceptions, and eliminates stdout parsing fragility. Codex CLI remains subprocess-based (no SDK equivalent). Prioritize when stdout parsing or process lifecycle issues surface in real engagements.

### Partial success states
Add `completed_with_warnings` status to stages. The metadata already captures counts (subagents succeeded/failed, coverage ratio), but the frontend doesn't distinguish "completed cleanly" from "completed with 60% coverage." Surface degraded state visually.
*Ref: suggestion #8*

### Failure-mode-aware retries
The design document distinguishes pre-execution, post-execution, and ambiguous failures for infra-hitting stages. The orchestrator currently retries generically. Encode retry semantics per failure class — don't auto-retry post-execution infra failures.
*Ref: suggestion #9*

### Local source snapshotting
For local paths, the system records the git commit but audits the live directory. If files change mid-run, reproducibility is lost. Copy or archive local source into a run-scoped snapshot directory. Record whether the source had uncommitted changes.
*Ref: suggestions #16, #17*

### Tool version capture
Record tool versions (ffuf, sqlmap, httpx, nmap, etc.) during setup. Include them in run metadata. Important for reproducibility of black-box engagements.
*Ref: suggestion #22*

### Run manifest
Write a single manifest per run containing: config snapshot, source metadata, tool versions, model names, prompt file hashes, concurrency settings, destructive policy, timestamps. One of the highest-value additions for production use.
*Ref: suggestion #54*

---

## Tier 2 — Workflow Improvements

Features that make the system more useful for real engagements.

### Re-hunt suggestions approval queue
The Bug Chainer generates re-hunt suggestions, but the UI currently treats re-hunts as free-form manual input. Show generated suggestions in a structured approval queue with approve/reject/edit actions.
*Ref: suggestion #35*

### Run delta vs cumulative display
The frontend should explicitly distinguish "this run found 3 new bugs" from "engagement total is 15 bugs." Show both values on run detail and engagement pages.
*Ref: suggestion #37*

### Cannot-validate workflow
Add structured reasons to the cannot-validate bin: destructive, insufficient access, flaky infra, model uncertainty, missing tooling. Let humans requeue specific items for re-validation.
*Ref: suggestion #44*

### Analyst notes and overrides
Allow human annotations on bugs, chains, and runs. Preserve model output and analyst override separately. Useful when the model gets severity wrong or misclassifies a finding.
*Ref: suggestion #43*

### Human review checkpoint
Make run end state `awaiting_review` until someone signs off. Separate machine-confirmed from human-reviewed findings in the final output.
*Ref: suggestion #40*

### Bug history / audit trail
Add an append-only event log for bug status transitions. Record transitions like `found → in_scope → validated → expanded → confirmed` with timestamps and model notes.
*Ref: suggestion #12*

---

## Tier 3 — Polish and UX

Frontend and operator experience improvements.

### Stage summary cards
Add per-stage health cards in the run detail view: attempts, success ratio, items processed, degraded flags, parse/schema errors.
*Ref: suggestion #32*

### Stage output browser filtering
Add JSON search/filter and raw vs. rendered summary views in the stage output browser.
*Ref: suggestion #33*

### Bug browser tabs
Separate canonical findings from merged/discarded noise with tabs: canonical, merged, discarded, cannot-validate, informational.
*Ref: suggestion #34*

### Engagement creation guidance
Add inline validation, examples, and presets for source audit vs black box. Warn about dangerous config combinations.
*Ref: suggestion #36*

### Live agent streaming structure
Add agent identity, current target/module, action type, and last activity timestamp to the streaming view.
*Ref: suggestion #38*

### Analyst handoff / review package
Add a "review package" view: source snapshot, confirmed bugs, cannot-validate, chains, unresolved questions. Exportable.
*Ref: suggestion #39*

---

## Tier 4 — Production Hardening

For when this moves beyond single-user local use.

### Session-based auth
Replace password reuse with a real login flow. Issue short-lived session tokens or signed cookies. Use `HttpOnly` cookies. Avoid query-string credentials for WebSockets.
*Ref: suggestions #6, #28*

### Secret handling
Encrypt `infra_config` at rest. Redact secrets in logs, event streams, and stage output browser. Add masking rules for credentials and tokens.
*Ref: suggestion #26*

### Multi-user / operator identities
Add named users or operator identities. Attribute engagement creation, approvals, and re-hunts to a user.
*Ref: suggestion #24*

### Authorization model
Add roles (viewer, operator, admin). Restrict high-risk actions separately from read-only browsing.
*Ref: suggestion #25*

### Data retention controls
Add retention policies and purge controls. Support auto-expiry for raw evidence and temporary artifacts.
*Ref: suggestion #27*

### CORS and cookie security
Lock CORS origins to configured values. Plan for `HttpOnly`, `Secure`, and `SameSite` cookies if moving to cookie auth.
*Ref: suggestions #29, #30*

---

## Tier 5 — Future Features

Product-level features for mature usage.

### Engagement baselines
Support baseline comparisons across runs: new findings, fixed findings, regressed findings, repeated intelligence.
*Ref: suggestion #55*

### Campaign-level metrics
Track: findings per run, validator pass rate, dedupe compression rate, triage retention rate, cost per confirmed bug, time per stage.
*Ref: suggestion #42*

### Confidence budget / workflow modes
Allow modes like: broad reconnaissance, conservative validation, high-confidence finalization. Tune stage aggressiveness accordingly.
*Ref: suggestion #41*

### Report generation
Generate audience-specific reports: analyst detail, management summary, remediation view, repro package.
*Ref: suggestion #49*

### Prompt versioning
Record prompt file hashes per run. Track prompt revisions across engagements. Compare "same target, different prompt generation."
*Ref: suggestion #50*

### Intelligence lifecycle
Distinguish environmental, auth, architecture, and exploit-enabling intelligence. Manage informational findings as structured assets.
*Ref: suggestion #45*

### Typed re-hunt hypotheses
Store re-hunt suggestions as: target area, bug class, chain objective, rationale, priority, required preconditions.
*Ref: suggestion #46*

### Target safety rails (black box)
Support allowlists, rate limits, quiet hours, destructive exclusions, and path-level exclusions for active testing.
*Ref: suggestion #47*

### Analyst-defined exclusions
Support exclusions for paths, hosts, vulnerability classes, auth flows, and known noisy areas.
*Ref: suggestion #48*

### Quarantine browser
Add a quarantined-artifact area for malformed model outputs. Persist raw output, parse errors, schema violations, and prompt metadata. Make browsable in the frontend.
*Ref: suggestion #11*

### Superseded finding semantics
Distinguish `duplicate`, `superseded`, `retracted`, and `discarded`. Preserve lineage from merged findings to canonical findings.
*Ref: suggestion #13*

### Evidence standardization
Separate "claim," "evidence," "PoC artifact," and "execution transcript" into first-class reportable objects shared between Validator and Perfectionist.
*Ref: suggestion #15*

### Bug survival narratives
Include per-bug summaries: why found, why in scope, why validated, why not discarded as contrived.
*Ref: suggestion #52*

### Operator trust indicators
Surface: low coverage, many retries, heavy fallback behavior, parsing failures, missing summaries.
*Ref: suggestion #53*

### Model comparison
Show which model ran each stage. Compare cost and output quality by model choice over time.
*Ref: suggestion #51*

### Environment profiles
Add profiles like `dev`, `safe-demo`, `production`, `disposable-lab` that influence destructive policy, auto-install, concurrency, and tool availability.
*Ref: suggestion #23*

### Install policy by trust domain
Separate `auto_install_os_packages`, `auto_install_python_tools`, `auto_install_go_tools`.
*Ref: suggestion #21*
