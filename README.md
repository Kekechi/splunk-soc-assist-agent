# soc-assist — an AI SOC copilot for Splunk

**Turn a Splunk alert into a guided, plain-language investigation — under least
privilege, with the agent auditing its own actions.**

soc-assist pings a junior analyst in Slack when a detection fires, explains *what*
fired and *why* in plain English, renders the evidence as a **native Splunk
Dashboard Studio** view, and reaches a severity verdict that cites its reasoning —
walking the analyst to resolution through Splunk's own surfaces. It **assists** a
real analyst; it does not replace Splunk, hide it, or reinvent its dashboards.

The thesis: **the harness *is* the senior engineer.** A senior brings two things to
a junior — *judgment* (an investigative playbook, triage, severity-with-a-reason)
and *guardrails* (least privilege, human sign-off, an audit trail). soc-assist
encodes both. The judgment is the value; the guardrails are the proof that an AI can
be trusted to act in production. Governing the AI *is* the security work.

> Built **environment-agnostic**: it assumes "a Splunk instance with the MCP server,"
> not any specific deployment. See **[DESIGN.md](DESIGN.md)** for the full rationale
> and **[ARCHITECTURE.md](ARCHITECTURE.md)** for the diagram and data flow.

## Demo

📺 **[3-minute demo video](#)** — *(link to be added)*

The investigation arc the video walks through:

1. An attacker brute-forces SSH; a saved-search **detection fires** in Splunk.
2. The agent picks up the alert and **investigates read-only** — corroborating
   queries against Splunk via the MCP server, each SPL explained in plain language.
3. It returns a **verdict with a severity and a stated reason**, narrated in Slack.
4. To publish evidence it must pass a **human approval gate** → on *yes*, a native
   Dashboard Studio view is created and its URL returned.
5. Every step — every query, gate decision, and write — lands in a dedicated Splunk
   audit index. **The watcher is watched.**

## Why it's different — governance as the feature

The AI-specific threat is a **compromised or prompt-injected agent**, not just a
leaked credential. Most agentic tooling over-privileges the agent and doesn't notice.
soc-assist treats confinement as the product:

- **The LLM never holds Splunk write credentials.** It can only *invoke* one
  in-process write tool, whose code can physically only POST Dashboard Studio JSON to
  **one app's** `data/ui/views` — nowhere else.
- **Every write passes a `can_use_tool` gate** that (a) **hard-rejects out-of-scope
  targets before any human is asked** — so a persuaded human can't bless an escape —
  and (b) requires an explicit human *yes*, defaulting to deny on timeout, EOF, or
  ambiguity. Even a fully prompt-injected agent cannot exceed the one-app scope.
- **The reasoning backend is swappable behind that gate.** Claude does NL→SPL and
  SPL→plain-English natively today; *Splunk AI Assistant for SPL* (`saia_*`) or any
  other agentic backend drops in behind the same `can_use_tool` boundary. soc-assist
  is the **governance layer you wrap around an agent**, not a competitor to one.

`python -m soc_assist.demo_gate` is a runnable, offline proof that the gate can't be
exceeded.

## Architecture

Four planes, **separated by privilege** — the full diagram, mermaid source, and data
flow are in **[ARCHITECTURE.md](ARCHITECTURE.md)** / [`architecture.svg`](architecture.svg):

| Plane | What | Privilege |
|---|---|---|
| **READ** | Native Splunk **MCP server**, consumed as an external MCP client (`mcp__splunk__*`) | read-only, broad |
| **WRITE** | One in-process `@tool` that POSTs a dashboard to a single app | the *only* elevated capability; gated |
| **NOTIFY** | The `Surface` seam — Slack (Socket Mode) or terminal; analyst-signal vs. operator-trail channels | zero Splunk privilege |
| **AUDIT** | Every gate decision and write appended to a dedicated index over HEC, with its own dashboard | — |

Built on the **Claude Agent SDK (Python)**; everything maps 1:1 onto SDK primitives
(`mcp_servers`, `allowed_tools`, the `can_use_tool` callback, an `@tool` write server).
No web framework, no inbound server, no database — **Splunk is the datastore and the
audit log.**

## Prerequisites

You supply a Splunk environment and credentials — nothing is hardcoded.

- A reachable **Splunk Enterprise/Cloud** over HTTPS (reference target: Enterprise 10.2.x).
- The **MCP Server for Splunk** app installed and reachable; the service account's
  role has the `mcp_tool_execute` capability.
- A **service account / token** for the harness: read + search via MCP, and a write
  path to one **app context** that will own generated dashboards. *(Hardening: a custom
  Splunk role scoping that write to a single app.)*
- A **Splunk index** for audit events, and **one or more log sources + a detection**
  to fire the demo event.
- A **Slack workspace** + bot token (optional; the run also works in the terminal).
- **Claude access** — a **Pro/Max subscription** (`claude setup-token` →
  `CLAUDE_CODE_OAUTH_TOKEN`) **or** an **Anthropic API key** (metered).
- *(optional)* **Splunk AI Assistant for SPL** for the `saia_*` tools — the harness
  defaults to Claude-native SPL and does **not** require it.

## Setup

```bash
# Python 3.10+
python -m venv .venv && source .venv/bin/activate
pip install -e .              # core: Claude Agent SDK + python-dotenv
pip install -e '.[write]'     # the gated REST write tool (httpx)
pip install -e '.[slack]'     # the Slack surface (slack-bolt)
pip install -e '.[all]'       # everything, for local dev

cp .env.example .env
$EDITOR .env                  # Claude auth + SPLUNK_MCP_* + write/audit/notify vars
```

**Claude auth — pick one** (see `.env.example`):
- *Subscription (Pro/Max):* install the Claude Code CLI and run `claude` to log in,
  or `claude setup-token` and set `CLAUDE_CODE_OAUTH_TOKEN`. No API key.
- *API key:* set `ANTHROPIC_API_KEY` from `platform.claude.com` (metered, separate billing).

### Configuration — two files, by concern

- **`.env`** — secrets and endpoints, grouped by plane (read / write / notify /
  audit). Fully annotated in [`.env.example`](.env.example).
- **Deployment profile** — *what to detect and where* (index, sourcetype, detection
  SPL, entity fields, a plain-language pipeline description). Set `SOC_ASSIST_PROFILE`
  to a TOML file; [`profiles/example.toml`](profiles/example.toml) is the
  env-agnostic template. **Unset → the built-in brute-force demo**, so the offline
  run needs zero config. Kept separate from `.env` so a real deployment retargets the
  data plane without touching secrets.

## Run it

```bash
# one-time provisioning against your Splunk instance:
SPLUNK_URL=https://your-splunk:8089 SPLUNK_AUTH=admin:changeme ./scripts/provision.sh
#   -> creates app soc_assist, the detection saved search, and the audit index
#   then fill the WRITE/AUDIT vars in .env (least-priv role recipe is in the script)

# load the sample dataset (sample-data/auth_bruteforce.log -> index=auth):
SPLUNK_URL=https://your-splunk:8089 SPLUNK_AUTH=admin:changeme ./scripts/load_sample.sh

# the full demo arc — detection fires -> investigate -> verdict -> gated publish:
python -m soc_assist.run --live
```

Run the pieces individually:

```bash
python -m soc_assist.investigate   # read-only investigation of the fixture alert
python -m soc_assist.run           # fixture alert, full gated run (terminal)
python -m soc_assist.run slack     # same, narrated in Slack: a clean analyst thread,
                                   # with the verbose operator trail in SLACK_DEBUG_CHANNEL
                                   # (or folded into the analyst thread if unset)
python -m soc_assist.demo_gate     # offline proof the gate can't be exceeded
```

Every dashboard write pauses for an explicit human **yes** — that's the point. The
agent's own actions land in `index=soc_audit` and the `soc_assist_audit_trail`
dashboard.

### Verify your Splunk MCP connection

`spike.py` is a minimal smoke test of the riskiest integration — the SDK talking to
the Splunk MCP server — and nothing more. A green run prints an MCP status line, a
`[tool]` line for `mcp__splunk__splunk_run_query`, and a plain-language summary of
five `index=_internal` events.

```bash
python spike.py
```

If it fails, the output points at the cause:
- **server `failed`** → check `SPLUNK_MCP_URL`, `SPLUNK_MCP_TRANSPORT` (`http` vs
  `sse`), and the auth header. *(Servers report `pending` at init — only `failed` is
  a real error.)*
- **no `[tool]` line** → the agent saw no tools; confirm `allowed_tools` uses the
  `mcp__splunk__*` wildcard and the role has `mcp_tool_execute`.

## Repo layout

```
src/soc_assist/      the harness — alert · investigate · gate · write_tool · audit · surface · slack_app
profiles/            deployment profiles (example.toml is the env-agnostic template)
sample-data/         the committed brute-force dataset (auth_bruteforce.log)
scripts/             provision.sh (app + detection + audit index) · load_sample.sh
architecture.svg     the architecture diagram (rendered from architecture.mmd)
DESIGN.md            full design and rationale   ·   ARCHITECTURE.md   the diagram + planes
SPEC.md              the build ledger            ·   spike.py          the MCP smoke test
```

## License

[Apache-2.0](LICENSE).
