# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`soc-assist` is an AI SOC copilot built on the **Claude Agent SDK (Python)** + the
**Splunk MCP server**. It turns a Splunk alert into a guided, plain-language
investigation: Slack is the doorway, a generated native Splunk Dashboard Studio view
is the room. The whole point is that the agent runs under **least privilege and audits
its own actions** — governing the AI *is* the security work, so don't trade that away
for convenience.

Read `DESIGN.md` for the full rationale and `ARCHITECTURE.md` for the diagram. This is
a public, environment-agnostic hackathon repo (Apache-2.0, package `soc_assist`).

## Commands

```bash
# Setup (Python 3.10+; the active venv is .venv on 3.12)
python -m venv .venv && source .venv/bin/activate
pip install -e .                 # core: claude-agent-sdk + python-dotenv
pip install -e '.[write]'        # Phase 3 REST write tool (httpx)
pip install -e '.[slack]'        # Phase 4 Slack surface (slack-bolt)
pip install -e '.[all]'          # everything, for local dev

cp .env.example .env             # then fill SPLUNK_MCP_* + Claude auth

python spike.py                  # Phase 1 read-path check — keep this green
```

There is **no test suite, linter, or formatter configured yet** — don't invent
`pytest`/`ruff`/`make` commands that aren't wired up. The spike is the current smoke
test: a green run prints the `splunk` MCP server status, a `[tool]` line for
`mcp__splunk__splunk_run_query`, and a plain-language summary of `index=_internal`
events.

## Architecture — four planes separated by privilege

The harness is the Claude Agent SDK; everything maps 1:1 onto SDK primitives (this is a
well-designed harness, not a whole app — no web framework, no inbound server, no DB;
Splunk *is* the datastore and the audit log).

- **READ** — the native Splunk MCP server, read-only and broad. Consumed as an external
  MCP client via `ClaudeAgentOptions(mcp_servers={"splunk": {"type": "http", ...}})`.
- **WRITE** — the *only* elevated capability: one in-process `@tool` (via
  `create_sdk_mcp_server()`) that can physically only POST Dashboard Studio JSON to one
  app's `data/ui/views`. The LLM never holds Splunk write credentials. (Phase 3.)
- **NOTIFY** — Slack (Bolt); zero Splunk privilege. (Phase 4.)
- **AUDIT** — every agent action appended to a dedicated Splunk index; the watcher is
  watched. (Phase 6.)

The security gate is `can_use_tool`: on every write it (a) requires human approval and
(b) hard-rejects out-of-scope targets — so even a fully prompt-injected agent can't
exceed scope. When you reach Phase 3, that callback is the security core; treat
weakening it as a regression.

## Critical conventions (the silent-failure traps)

These are verified facts that cost real debugging time — get them wrong and the agent
*silently does nothing* rather than erroring:

- **Splunk MCP tools are prefixed `splunk_`** (e.g. `splunk_run_query`,
  `splunk_get_indexes`), namespaced for the SDK as `mcp__splunk__<tool>`. The SPL
  assistant tools are `saia_*` (require *Splunk AI Assistant for SPL* installed).
  **SAIA is optional, not a dependency:** the harness defaults to Claude-native SPL
  explain/generate (Claude already does NL→SPL and SPL→plain-English with full context),
  and `saia_*` is a swappable drop-in behind the same interface. Don't put cloud-tenant /
  SAIA provisioning on the critical path. See DESIGN.md §8.
  **Always use the wildcard `mcp__splunk__*` in `allowed_tools`** — a bare
  `mcp__splunk__run_query` matches nothing.
- **The Python SDK is snake_case**: `mcp_servers`, `allowed_tools`, `can_use_tool`,
  `PermissionResultAllow` / `PermissionResultDeny`. (DESIGN.md §6 writes them TS-style in
  prose only — the code is snake_case.)
- **HTTP transport string is `"http"`** (not `"streamable-http"`); SSE is `"sse"`.
- At the `init` `SystemMessage`, MCP servers report `pending`, not `connected` — only
  `failed` is a real error. The true success signal is the tool call + result.
- **Least-privilege hardening:** the SDK loads the user's global `~/.claude` settings by
  default, which leaks ambient MCP servers (e.g. a `memory` server) into the agent. Pass
  `setting_sources=[]` on `ClaudeAgentOptions` when hardening (Phase 3) for purity and
  reproducibility.

## Build plan & where code goes

Walking skeleton, riskiest-first (DESIGN.md §11). **Phases 0–1 are GREEN**; next is
**Phase 2 — console investigation loop** (feed a hard-coded alert context → agent
queries Splunk, explains the SPL in plain language (Claude-native; `saia_explain_spl` is
an optional drop-in), returns that explanation + severity-with-a-reason; no Slack yet).

`spike.py` is a throwaway Phase-1 proof. **Real functionality from Phase 2 onward lives
in `src/soc_assist/`**, not in new top-level spikes.

## Session docs are your external memory

`.claude/session/*.md` are gitignored (they hold deployment-specific detail) but they are
**written for you** — your durable, cross-session working memory. At the start of a
resumed session, **read the latest `.claude/session/` log** (and `CLAUDE.local.md`, which
is auto-loaded) to recover where the build is, what was decided, and what's parked. At the
end of a substantive session, append a new dated log: outcome, decisions, verified facts,
current state, open items, how-to-resume, and the next step. Match the format of the
existing logs. `SPEC.md` is the public build backlog; the session logs are the running
narrative behind it.

## Operating principles for long-running build sessions

Two disciplines keep a multi-phase build healthy, and they reinforce each other —
delegation is what makes thorough verification affordable without drowning the overseer.

- **Keep the orchestrator context lean; delegate one-shot noise to sub-agents.** The main
  session is the sprint overseer: it holds the `SPEC.md` ledger, the decisions, the phase
  state, and the session log — and little else. Offload work that spews bulky transient
  output — codebase fan-out searches, digesting long command/log output, an isolated
  build-and-verify chunk — to a sub-agent (the Agent tool; `Explore` for read-only search).
  The sub-agent does the noisy work and returns a **distilled conclusion + the evidence
  that matters**, not the raw dump. It does *not* share this context and only its final
  message comes back, so give it a self-contained brief (inputs, expected output) and have
  it report findings, not transcripts.

- **Verify against the real system; never advance on assumption.** Before marking any
  `[AUTO⚙]` unit done, actually run it — the SPL query, the REST call, the ssh check — and
  read what the live box says (the `.local/` wrappers against Splunk 10.2.2; see
  `CLAUDE.local.md`). This repo has already been bitten by assuming: the seed doc's MCP tool
  names were wrong and only a live call caught it. A unit is done when its done-criterion is
  *observed*, not when the code looks right.

- **Right-size investment to the hackathon horizon.** This is a ~5-day, demo-terminal
  project (deadline ≈ 2026-06-15), so front-load only what pays back **inside the window**
  (the `Surface` abstraction and `SPEC.md` earn their cost now). Prefer the **boring,
  accepted default over the clever or general one** — SDK primitives over a framework,
  config-as-env over a generator. The discipline isn't "pay now," it's knowing *which* stage
  deserves the spend: ship the smallest thing that meets a unit's done-criterion, and park
  the richer version as an `[AUTO]` item rather than building it speculatively.

## Repo constraints

- **Environment-agnostic and public.** Never add hostnames, IPs, tokens, or
  deployment-specific detail to committed files — DESIGN.md §1 forbids it. All config is
  env vars in `.env` (gitignored), documented in `.env.example`, grouped by plane
  (read / write / notify / audit).
- `.env` and `.claude/session/` hold real Splunk tokens and a deployment hostname and
  are gitignored — verify they stay out of any `git add -A`.
- **Claude auth** is either a Pro/Max subscription (`CLAUDE_CODE_OAUTH_TOKEN` via
  `claude setup-token`) or `ANTHROPIC_API_KEY` (metered). This dev uses the subscription.
