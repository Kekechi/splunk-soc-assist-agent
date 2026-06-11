# AI SOC Copilot — Design & Kickoff

> Seed document for a new, standalone public repository.
> Written to be **environment-agnostic**: it assumes "a Splunk instance with the MCP
> server," not any specific deployment. Anyone with Splunk + the MCP server should be
> able to run the result. Do not add network topology, hostnames, IPs, or
> deployment-specific detail to this repo.

---

## 1. The concept (one sentence)

**An AI copilot that turns a Splunk alert into a guided, plain-language investigation —
pinging a junior analyst in Slack, explaining what fired and why, rendering the evidence
as a native Splunk dashboard, and walking them to resolution through Splunk's own
features — so confusion becomes competence.**

It **assists** a real analyst using Splunk's own surfaces. It does not replace Splunk,
hide it, or reinvent its dashboards.

---

## 2. Positioning — why this, by this author

The author is *themselves* a newcomer to SOC/SIEM work who installed Splunk and felt the
"where do I even start?" confusion firsthand. That is the product's spine, not a caveat:

- Most security tooling is built by experts imagining a novice. This is built by someone
  with the novice's confusion still fresh — hard to fake, easy to feel in a demo.
- "What would have unstuck me on day one?" is a legitimate design-research method here.

The same artifact also tells a second story for free: a junior being walked through their
first alert *is* onboarding. We **narrate** that value; we don't build a separate
onboarding product (keeps scope hackathon-sized).

---

## 3. Hackathon fit

- **Track:** Security (primary). The solution helps a security team detect faster,
  investigate more efficiently, and automate workflows with AI on Splunk data.
- **A differentiator hiding in plain sight:** the agent runs under **least privilege and
  audits its own actions**. Governing the AI *is* security work — most entries will
  over-privilege their agent and not notice.
- **Required deliverables (track against these):**
  - [ ] Text description of features/functionality
  - [ ] Demo video < 3 min (problem, AI usage, value; runs on target)
  - [ ] Public OSS repo: license, all source, README with setup/run, deps, example
        config, sample dataset
  - [ ] Architecture diagram in repo root (how it talks to Splunk, how AI is integrated,
        data flow)

---

## 4. Locked decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Standalone repo**, not coupled to any homelab | Reproducibility (judging), clean security boundary, honest "anyone can run it" claim |
| D2 | Build **only the incident-investigation arc**; narrate onboarding value | Keeps scope demo-sized; the arc already demonstrates orientation |
| D3 | **Slack as the doorway, a generated Splunk dashboard as the room** | Uses Splunk's own surfaces end-to-end; minimal reinvention; demos well |
| D4 | **Least-privilege, baked in** (read-only MCP + one gated write tool + audit) | On-theme security pillar; nearly free given the architecture |
| D5 | **Pull from Splunk, push to Slack** | No inbound infra; portable; still proactive ("it finds you") from the user's view |
| D6 | **Harness = Claude Agent SDK**, Python | Agent loop + MCP client + custom tools + permission gate + service runtime, out of the box |
| D7 | The harness identity is a **runtime service principal**, distinct from any developer's permissions | Different trust model; part of the security story |

**Still open (deliberately parked — neither destabilizes the architecture):**
- The **attack content** that drives the demo (candidates: DNS exfil — best AI-value
  story; auth brute-force — most legible). Decide later; the pipeline doesn't depend on
  which.
- The **LLM model** (Claude; Sonnet for cost/latency vs Opus for hardest reasoning).

---

## 5. Architecture

The demo arc: **attack sim → Splunk-native detection fires → harness pulls it → Slack
ping → assisted investigation → (approved) dashboard rendered → resolution + summary.**

Four planes, separated by privilege:

```
[Attack sim] → log pipelines (DNS / auth / firewall / …) → Splunk indexes
                                                                │
                            Splunk-native detection (a saved search / alert you wrote)
                                                                │ fires
                                                                ▼
                                                    [ Harness — Claude Agent SDK ]
                                ┌───────────────────────────────┼────────────────────────────┐
                          READ plane                        WRITE plane                   NOTIFY plane
                   Splunk MCP server (read-only):      one in-process @tool:          Slack (Bolt):
                   run_query, get_indexes,             POST Dashboard Studio JSON      notify + investigation
                   saia_explain_spl, …                 to data/ui/views                thread. No Splunk
                   (mcp__splunk__*)                    (scoped to ONE app)             privilege at all.
                                │                            │
                                │                      canUseTool gate:
                                │                      (a) human approval
                                │                      (b) hard scope check
                                └───────────────────────────┼────────────────────────────┘
                                                            ▼
                                          AUDIT plane: agent actions → a Splunk index
                                          (the watcher is watched)
```

- **Read** — native MCP, read-only, broad. Free; the SDK consumes it as a client.
- **Write** — the *only* elevated capability in the system. One narrow tool.
- **Notify** — Slack; zero Splunk privilege.
- **Audit** — append-only; the agent's own actions become SIEM data.

**Deliberately NOT in the stack** (the "don't reinvent" win):
- No web framework — Slack + Splunk dashboards are the face.
- No inbound server — pull, so nothing to expose.
- No database — Splunk *is* the datastore and the audit log.

---

## 6. Claude Agent SDK → architecture mapping (all verified, see §9)

| Need | SDK primitive |
|---|---|
| Connect to the **Splunk MCP server** as a client | External MCP server via `mcpServers` (HTTP); tools namespaced `mcp__splunk__*`; enable with `allowedTools: ["mcp__splunk__*"]` |
| **Human-approval gate** + **hard scope check** on writes | `canUseTool` callback — intercepts each call at runtime, sees the full `mcp__`-prefixed name, can allow / deny / modify |
| Custom **Splunk REST write tool** | `@tool` decorator → `create_sdk_mcp_server()` → passed to `ClaudeAgentOptions.mcp_servers` |
| Run as a **long-running service** beside Slack | `query()` / `ClaudeSDKClient` |
| Install | `pip install claude-agent-sdk` |

The whole architecture maps 1:1 onto SDK primitives — hence "well-designed harness, not
a whole app."

---

## 7. Security / least-privilege model

The AI-specific threat is a **compromised or prompt-injected agent**, not (only) a leaked
credential. Enforce scope at two layers (defense in depth):

1. **Harness-side capability confinement (required for the PoC).** The LLM never holds
   Splunk write credentials. It can only *invoke* the write `@tool`, whose code can
   physically only POST to one app's `data/ui/views`, validates the target, and refuses
   anything else. Every call passes through `canUseTool` — which (a) requires human
   approval for writes and (b) rejects out-of-scope targets. *Even a fully prompt-injected
   agent cannot exceed this scope.* This is the object-capability model, in ~20 lines.

2. **Splunk-side role IAM (documented as production hardening).** A custom Splunk role on
   the service account, limited to writing views in one app — bounds the blast radius if
   the credential itself leaks. Fiddly to set up; *not required* for the PoC because the
   harness tool already bounds the action. Describe it; optionally do a minimal version.

**Elegant coincidence to lean on in the demo:** the approval gate and the "junior makes a
decision" teaching beat are the *same moment*. Security control = pedagogy.

**Audit:** log every agent action (queries run, tools invoked, dashboards created,
approvals) to a dedicated Splunk index. The agent that investigates the SIEM becomes
observable *inside that same SIEM*.

---

## 8. Tech stack

- **Language:** Python (Agent SDK is Python-native; Splunk SDK + Slack Bolt are Python — one ecosystem).
- **Harness:** `claude-agent-sdk` — agent loop, MCP client, custom tools, permission gate, service runtime.
- **Read:** Splunk MCP server over HTTP (read-only by design). Requires *Splunk AI
  Assistant for SPL* installed for the `saia_*` explain/generate tools — **verify this is
  installed**.
- **Write:** one in-process `@tool` → Splunk REST `data/ui/views` (Splunk SDK for Python or `httpx`).
- **Enforcement:** `canUseTool` callback (approval + single-app scope check).
- **Notify/investigate:** Slack Bolt for Python.
- **Rendered face:** native Splunk Dashboard Studio (`<dashboard version="2">` + JSON in CDATA).
- **LLM:** Claude (model deferred — Sonnet for cost/latency, Opus for hardest reasoning; SDK selects via options).

---

## 9. Verified facts (Known) and what still needs verifying

**Known — verified against official docs:**
- Splunk MCP server is **read/search-only**; no create/update tools for dashboards,
  saved searches, alerts, or knowledge objects.
  → https://help.splunk.com/en/splunk-enterprise/mcp-server-for-splunk-platform/mcp-server-tools
- `saia_*` tools (generate/explain/optimize SPL, ask question) require **Splunk AI
  Assistant for SPL** installed.
- Dashboards support full CRUD via REST `POST …/data/ui/views`, Dashboard Studio format,
  documented for **Splunk Enterprise 10.2**.
  → https://help.splunk.com/en/splunk-enterprise/create-dashboards-and-reports/dashboard-studio/10.2/manage-dashboards/create-a-dashboard-using-rest-api-endpoints
- Claude Agent SDK: external MCP servers, `canUseTool`, `@tool` + `create_sdk_mcp_server`,
  `query()`/`ClaudeSDKClient`, `pip install claude-agent-sdk`.
  → https://docs.claude.com/en/docs/agent-sdk/mcp
  → https://platform.claude.com/docs/en/agent-sdk/permissions
  → https://platform.claude.com/docs/en/agent-sdk/custom-tools
  → https://docs.claude.com/en/docs/agent-sdk/python

**To verify before relying on it (Hypothesised):**
- The exact **Splunk role capability** required to write views via REST, and whether it
  scopes cleanly to a single dashboard/app. (Only matters if you do the Splunk-side IAM
  layer; not blocking for the PoC.)
- The Splunk MCP server's **HTTP endpoint + auth** mechanism as configured in your
  environment, and that the service role has `mcp_tool_execute`.
- That **AI Assistant for SPL** is installed (gates the `saia_*` tools).
- The **TypeScript** package name if you ever go TS (Python is the recommendation).

---

## 10. What the harness needs from a Splunk environment

State these as **prerequisites** in the README — abstract, not deployment-specific:

- A reachable **Splunk Enterprise/Cloud** (reference target: Enterprise 10.2.x) over HTTPS.
- The **MCP Server for Splunk platform** app installed and reachable; service account has
  `mcp_tool_execute`.
- **Splunk AI Assistant for SPL** installed (for `saia_*` tools).
- A **service account / token** for the harness:
  - read + search (via MCP),
  - a write path to one **app context** that will own generated dashboards,
  - (optional, hardening) a custom role scoping that write to a single app.
- A **Splunk index** to receive audit events.
- One or more **log sources + a detection** (saved search/alert) to fire the demo event.
- A **Slack workspace** + bot token (Bolt app).
- An **Anthropic API key** for the Agent SDK.

All supplied via env/config — nothing hardcoded.

---

## 11. Proposed build plan — walking skeleton, riskiest-first

Build the thinnest end-to-end slice, then thicken. De-risk the novel integrations before
investing in polish.

- **Phase 0 — Scaffold.** Repo, OSS license, `pyproject`, `README` (prereqs from §10),
  `.env.example`, this doc as `DESIGN.md`, a placeholder architecture diagram.
- **Phase 1 — Prove the read path (riskiest).** Agent SDK connects to the Splunk MCP
  server, runs one `run_query`, prints results. Confirms connectivity + auth + the SDK↔MCP
  link. *Stop here until this works — everything depends on it.*
- **Phase 2 — Investigation loop (console).** Feed the agent a hard-coded "alert context";
  it queries, uses `saia_explain_spl`, and returns a plain-language explanation +
  severity-with-a-reason. No Slack yet.
- **Phase 3 — Write tool + gate (the security core).** One `@tool` that creates a Splunk
  dashboard via REST. Wire `canUseTool` to require approval and enforce single-app scope.
  Demonstrate a rejected out-of-scope write. This is the least-privilege story working.
- **Phase 4 — Slack surface.** Move notify + investigation into a Slack thread; approvals
  happen in-thread; post the dashboard link.
- **Phase 5 — Attack sim + detection.** Script a safe, reproducible event into a log
  source; write the Splunk detection that fires; have the harness pull it. (Pick the
  attack content here.)
- **Phase 6 — Audit plane.** Log agent actions to the Splunk audit index; optionally show
  them surfaced in Splunk.
- **Phase 7 — Polish + submission.** Real architecture diagram, README run instructions,
  sample dataset, < 3-min demo video (show the "without AI vs with AI" contrast).

**Why this order:** Phases 1 and 3 are the only genuinely uncertain integrations (SDK↔MCP
auth; REST write + gating). Everything else is conventional. Failing fast on those two
protects the timeline.

---

## 12. First concrete steps

1. Create the repo; drop in this file as `DESIGN.md`, add an OSS license (MIT or Apache-2.0).
2. `pip install claude-agent-sdk`; set `ANTHROPIC_API_KEY`.
3. Confirm the Splunk MCP server endpoint + token, and that AI Assistant for SPL is installed (§9 to-verify).
4. Write the Phase 1 spike: SDK + `mcpServers` pointing at Splunk MCP, `allowedTools:
   ["mcp__splunk__*"]`, run one query, print results. **Make this green before anything else.**
```
