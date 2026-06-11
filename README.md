# soc-assist — an AI SOC copilot for Splunk

**Turn a Splunk alert into a guided, plain-language investigation.** soc-assist
pings a junior analyst in Slack, explains what fired and why, renders the
evidence as a native Splunk dashboard, and walks them to resolution through
Splunk's own surfaces — so confusion becomes competence.

It **assists** a real analyst using Splunk's own features. It does not replace
Splunk, hide it, or reinvent its dashboards. And it does its work under **least
privilege, auditing its own actions** — governing the AI *is* the security work.

> Built environment-agnostically: it assumes "a Splunk instance with the MCP
> server," not any specific deployment. See [DESIGN.md](DESIGN.md) for the full
> design and [ARCHITECTURE.md](ARCHITECTURE.md) for the diagram.

## Status

Walking skeleton, built riskiest-first.

- [x] **Phase 0 — Scaffold.** This repo: license, packaging, README, config, design doc, diagram placeholder.
- [x] **Phase 1 — Prove the read path** (`spike.py`). SDK ↔ Splunk MCP, one query, real results.
- [x] **Phase 2 — Investigation loop (console).** `python -m soc_assist.investigate`: alert in, plain-language verdict out, severity citing the rubric.
- [x] **Phase 3 — Write tool + `can_use_tool` gate (the security core).** `python -m soc_assist.run`: gated, human-approved dashboard publishing; `python -m soc_assist.demo_gate`: the out-of-scope rejection proof.
- [ ] **Phase 4 — Slack surface.** Code complete (`python -m soc_assist.run slack`); awaiting a Slack app + tokens to verify live.
- [ ] **Phase 5 — Attack sim + detection.**
- [ ] **Phase 6 — Audit plane.**
- [ ] **Phase 7 — Polish + submission.**

## Prerequisites

You supply a Splunk environment and credentials via `.env` — nothing is hardcoded.

- A reachable **Splunk Enterprise/Cloud** (reference target: Enterprise 10.2.x) over HTTPS.
- The **MCP Server for Splunk platform** app installed and reachable; the service
  account's role has the `mcp_tool_execute` capability.
- *(optional)* **Splunk AI Assistant for SPL** installed — enables the `saia_*`
  explain/generate tools. The harness defaults to **Claude-native** SPL explain/generate
  and does **not** require it (SAIA may need a Cloud entitlement; not worth blocking on).
- A **service account / token** for the harness:
  - read + search (via MCP),
  - a write path to one **app context** that will own generated dashboards (Phase 3),
  - *(optional hardening)* a custom Splunk role scoping that write to a single app.
- A **Splunk index** to receive audit events (Phase 6).
- One or more **log sources + a detection** (saved search/alert) to fire the demo event (Phase 5).
- A **Slack workspace** + bot token (Phase 4).
- **Claude access** for the Agent SDK — either a **Claude Pro/Max subscription**
  (log in via the Claude Code CLI, or `claude setup-token` for a headless token)
  **or** an **Anthropic API key** (`platform.claude.com`, metered, separate billing).

## Setup

```bash
# Python 3.10+
python -m venv .venv && source .venv/bin/activate
pip install -e .            # core deps (Agent SDK + dotenv)
# later phases:
#   pip install -e '.[write]'   # Phase 3 REST write tool (httpx)
#   pip install -e '.[slack]'   # Phase 4 Slack surface (slack-bolt)
#   pip install -e '.[all]'

cp .env.example .env
$EDITOR .env                # set Claude auth + the SPLUNK_MCP_* values
```

**Claude auth — pick one** (see `.env.example`):
- *Subscription (Pro/Max):* `npm`/`curl`-install the Claude Code CLI, then `claude`
  to log in — or `claude setup-token` and set `CLAUDE_CODE_OAUTH_TOKEN`. No API key.
- *API key:* set `ANTHROPIC_API_KEY` from `platform.claude.com` (metered, separate billing).

## Run the Phase 1 spike

The spike proves the riskiest integration — the SDK talking to the Splunk MCP
server — and nothing more. Make it green before building anything downstream.

```bash
python spike.py
```

Expected: a line reporting the `splunk` MCP server as `connected`, a `[tool]`
line showing `mcp__splunk__splunk_run_query`, and a plain-language summary of
five `index=_internal` events.

If it fails, the spike output points at the cause:
- **server not `connected`** → check `SPLUNK_MCP_URL`, `SPLUNK_MCP_TRANSPORT`
  (`http` vs `sse`), and the auth header (`SPLUNK_MCP_AUTH_SCHEME` + token).
- **no `[tool]` line** → the agent saw no tools; confirm `allowed_tools` matches
  the real tool names (they're prefixed `splunk_`, hence the `mcp__splunk__*`
  wildcard) and that the role has `mcp_tool_execute`.

## Configuration

All configuration is via environment variables — see [`.env.example`](.env.example)
for the full annotated list, grouped by plane (read / write / notify / audit).

## Security model

The AI-specific threat is a **compromised or prompt-injected agent**, not only a
leaked credential. Two layers of defense:

1. **Harness-side capability confinement (in the PoC).** The LLM never holds
   Splunk write credentials. It can only *invoke* the write `@tool`, whose code
   can physically only POST to one app's `data/ui/views`. Every call passes
   through `can_use_tool`, which (a) requires human approval and (b) rejects
   out-of-scope targets. Even a fully prompt-injected agent cannot exceed this.
2. **Splunk-side role IAM (documented as production hardening).** A custom role
   bounding the service account's write to a single app, limiting blast radius
   if the credential itself leaks.

Every agent action (queries, tool calls, dashboards created, approvals) is logged
to a dedicated Splunk index — the agent that investigates the SIEM becomes
observable inside that same SIEM.

## License

[Apache-2.0](LICENSE).
