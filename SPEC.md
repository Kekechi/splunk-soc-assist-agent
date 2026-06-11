# SPEC — decision-complete build ledger

> Companion to `DESIGN.md` (the *why*) and `ARCHITECTURE.md` (the *shape*). This is the
> *exactly what*, written so it can be executed as a long-running task with minimal
> human turns. Each unit has a concrete done-criterion and is tagged:
>
> - **`[AUTO]`** — buildable autonomously (code, local verification).
> - **`[AUTO⚙]`** — autonomous but needs the **SSH key** to verify/provision against
>   the live Splunk instance. Blocked until SSH is in hand.
> - **`[HUMAN]`** — requires a human turn that no key fixes (browser OAuth, recording,
>   final taste). Park and flag.
>
> Resolved decisions baked in (2026-06-10): attack = **auth brute-force**; write/audit
> Splunk path = **figure out live over SSH**; SPL explain = **Claude-native** (SAIA
> optional). Deadline ≈ **2026-06-15** — bias every call toward the demo path.

---

## How this runs as a long-running task

The agent works top-to-bottom. `[AUTO]` units run unattended. On hitting an `[AUTO⚙]`
unit with no SSH yet, or any `[HUMAN]` unit, it **parks that unit, records what it needs,
and continues to the next independent unit** rather than blocking. A unit is *done* only
when its done-criterion is met and (for code) it imports/runs clean.

**The structural ceiling (not a tooling gap):**
- Slack app creation + tokens, and the demo recording, are `[HUMAN]`. Always.
- The *running product* keeps a human in the write-approval loop **by design** — that
  approval gate is the security thesis, so "fully autonomous at runtime" is a non-goal,
  not a limitation.

**Human-input queue** (collect these once; they unblock the most units):
1. SSH access to the Splunk host → unblocks all `[AUTO⚙]`.
2. Slack app: bot token + app token (Socket Mode) → unblocks Phase 4.
3. Final review of severity rubric wording + demo script → Phase 7.

---

## Phase 2 — Console investigation loop  `[AUTO]`

Goal: hard-coded alert context → agent investigates read-only → structured, plain-language
verdict. No Slack, no writes.

**2.1 Alert-context schema** `[AUTO]` — `src/soc_assist/alert.py`
A frozen dataclass `AlertContext` carrying what a fired detection hands the agent:
`rule_name, rule_id, detection_spl, fired_at (iso), index, severity_hint, entities
(dict: e.g. src_ip, user), observed_count, earliest/latest`. Ship one brute-force
fixture (`BRUTE_FORCE_FIXTURE`): ~50 failed SSH logins from one src_ip against one user,
then one success.
*Done:* `from soc_assist.alert import BRUTE_FORCE_FIXTURE` imports and is fully populated.

**2.2 System prompt + persona** `[AUTO]` — `src/soc_assist/prompts.py`
Persona: a calm senior analyst mentoring a junior. Defines: task (explain *what* fired
and *why*, in plain language; triage it), tool budget (read-only `mcp__splunk__*` only),
SPL is explained Claude-native, and the output contract (2.4). No flattery, no jargon
without a gloss.
*Done:* constant `INVESTIGATION_SYSTEM_PROMPT` exists and references the output contract.

**2.3 Severity rubric** `[AUTO]` — encoded in the prompt + `alert.py`
Deterministic, defensible ladder so severity isn't vibes:
`info` (failures only, non-privileged, isolated) → `low` → `medium` (sustained burst,
single account) → `high` (**failures followed by a success** = likely compromise) →
`critical` (success + privileged/admin account, or lateral spread). The agent must cite
which rung and why.
*Done:* rubric written; `[HUMAN]` wording review deferred to 7.x (non-blocking).

**2.4 Investigation loop** `[AUTO]` — `src/soc_assist/investigate.py`
`async def investigate(alert: AlertContext) -> Investigation`. Uses `ClaudeSDKClient`
with read-only `allowed_tools=["mcp__splunk__*"]`, the 2.2 prompt, and the alert injected
as the opening user turn. Agent runs its own follow-up SPL (e.g. confirm the success,
enumerate other targets from that src_ip). Returns `Investigation`: `summary` (plain
language), `severity` + `reason`, `queries_run` (list), `recommended_next_steps`.
Enforce the structured tail via a final "emit your verdict as JSON" turn or a tiny
structured-output tool.
*Done:* `python -m soc_assist.investigate` prints a populated `Investigation` for the
brute-force fixture against real Splunk; severity reasoning cites the rubric rung.

**2.5 Harden options** `[AUTO]` — pass `setting_sources=[]` so the ambient `~/.claude`
`memory` MCP server can't leak in (observed in the spike). Apply here and everywhere the
real harness constructs options.
*Done:* server list in a debug run shows only `splunk`.

**2.6 Notify/approve surface — the Slack seam** `[AUTO]` — `src/soc_assist/surface.py`
*(makes Slack a swappable backend, not a load-bearing dependency — the whole app runs
end-to-end before Slack exists).*
Abstract `Surface` with async methods: `notify(text)`, `stream(text)` (post incremental
investigation output), and `request_approval(proposal) -> bool`. First implementation
`CliSurface` — prints to stdout, reads stdin y/n for approval. `SlackSurface` (Phase 4)
implements the *same* interface behind the async bridge. The gate (3.3) and the
investigation loop depend only on `Surface`, never on Slack directly.
*Done:* `CliSurface` drives a full fixture run incl. an approval prompt, no Slack imported.

---

## Phase 3 — Write tool + `can_use_tool` gate (the security core)  `[AUTO]` / `[AUTO⚙]`

Goal: one narrow write capability, two-layer gated. **Treat weakening the gate as a
regression.**

**3.1 Write tool** `[AUTO]` — `src/soc_assist/write_tool.py`
`@tool` `create_dashboard(name: str, definition: dict)` via `create_sdk_mcp_server`.
The **app context is read from env (`SPLUNK_WRITE_APP`) and hard-coded into the URL** —
`servicesNS/nobody/{app}/data/ui/views` — so the tool *physically cannot* target another
app regardless of arguments. POSTs with `httpx` using `SPLUNK_WRITE_TOKEN` (distinct from
the MCP token; the LLM never sees it). Returns the view URL on success.
*Done:* unit imports; with a stubbed HTTP layer, a happy-path call builds the correct
single-app URL and an attempt to smuggle a different app still resolves to the allowed one.

**3.2 Dashboard Studio JSON builder** `[AUTO⚙]` — `src/soc_assist/dashboard.py`
`build_brute_force_dashboard(investigation, alert) -> dict`: a Dashboard Studio v2
definition with 3 panels — single-value (total failed attempts), timechart (failures over
time), table (top source IPs / targeted users). **Exact JSON shape verified against the
live instance over SSH** (create one by hand, GET it back, mirror the structure). Until
then, build from the 10.2 REST docs and mark unverified.
*Done:* returns a dict that the live REST endpoint accepts and renders (⚙ verify).

**3.3 `can_use_tool` gate** `[AUTO]` — `src/soc_assist/gate.py`
The security core. On every tool call:
- If it is **not** the write tool → allow (read tools pass).
- If it **is** the write tool → (a) **hard scope check**: target app must equal
  `SPLUNK_WRITE_APP`, else `PermissionResultDeny` with a logged reason; (b) **human
  approval** via `surface.request_approval(...)` (2.6) — `CliSurface` now, `SlackSurface`
  later; the gate never knows which. Deny on anything but explicit yes.
*Done:* with an in-scope target + "yes" → allow; with "no" → deny; with a tampered
out-of-scope target → deny **without even asking** (scope beats approval).

**3.4 Rejection demo** `[AUTO]` — `src/soc_assist/demo_gate.py` (or a doc snippet)
Drive the agent (or a direct call) to attempt an out-of-scope write; show the gate
rejecting it. This is the "even a prompt-injected agent can't exceed scope" proof beat.
*Done:* a runnable that prints the denial with its reason.

---

## Phase 4 — Slack surface  `[AUTO]` code / `[HUMAN]` provisioning

Goal: a second `Surface` implementation (2.6) — Slack thread instead of CLI. Because the
app already runs end-to-end on `CliSurface`, this phase adds a backend, it does not unblock
the app. Keep **no inbound server** (D5) → **Socket Mode**.

**4.0 Slack app provisioning** `[HUMAN]` — create the app, scopes (`chat:write`,
`chat:write.public`, `commands` if used), enable Socket Mode, collect bot token
(`SLACK_BOT_TOKEN`) + app token (`SLACK_APP_TOKEN`). No code unblocks this.

**4.1 `SlackSurface` + async approval bridge** `[AUTO]` — `src/soc_assist/slack_app.py`
*(the genuinely hard part — design pinned so it isn't rediscovered live)*.
Implements `Surface`. `notify`/`stream` post to the thread. `request_approval` is the hard
one: it's awaited inside `can_use_tool`, but the click arrives on a separate Bolt handler.
Bridge: a module-level `pending: dict[correlation_id, asyncio.Future]`. `request_approval`
posts a Block Kit message (Approve / Reject buttons carrying the `correlation_id`), then
`await`s the Future; the `AsyncApp` action handler resolves it on click.
`AsyncSocketModeHandler` runs the Slack loop on the same event loop as the agent. Timeout
→ default-deny. Swapping `CliSurface`→`SlackSurface` is the only wiring change.
*Done:* same fixture run as 2.6, but in a Slack thread; a click unblocks the gate; timeout
denies safely.

---

## Phase 5 — Attack sim + detection (auth brute-force)  `[AUTO]` / `[AUTO⚙]`

**5.1 Synthetic dataset** `[AUTO]` — `sample-data/auth_bruteforce.log`
Committed, environment-agnostic: ~50 failed SSH auth lines from one src_ip against one
user over a few minutes, then one success. Realistic `sshd`/`auth.log` format. This is
the README's required sample dataset.
*Done:* file committed; parses as auth events.

**5.2 Ingest path** `[AUTO⚙]` — load 5.1 into Splunk live. Decide the mechanism over SSH
(HEC vs scripted/oneshot input into an `auth` index). Write `scripts/load_sample.sh` (or
`.py`) that any user can run against their own instance.
*Done:* events queryable as `index=<auth> sourcetype=...` (⚙ verify).

**5.3 Detection** `[AUTO⚙]` — the saved search/alert that fires:
`index=<auth> action=failure | stats count, values(action) as actions by src_ip,user |
where count > <threshold>`. Provision as a saved search over SSH; capture its fired
context into the real `AlertContext` (replaces the 2.1 fixture).
*Done:* the search fires on the loaded data and yields the alert context (⚙ verify).

---

## Phase 6 — Audit plane  `[AUTO]` code / `[AUTO⚙]` provisioning

**6.1 Audit emitter** `[AUTO]` — `src/soc_assist/audit.py`
`emit(action, *, tool, args_summary, decision, detail)` → structured JSON event to a
dedicated audit index via HEC (`SPLUNK_AUDIT_HEC_TOKEN`, `SPLUNK_AUDIT_INDEX`). Hook it
into `can_use_tool` (every allow/deny) and tool execution (every write).
*Done:* every gate decision and write produces one audit event (local: HEC stub).

**6.2 Audit index + HEC** `[AUTO⚙]` — create the index and HEC token over SSH; point
6.1 at them.
*Done:* `index=<audit>` shows real agent-action events; optionally a panel surfaces them
("the watcher is watched") (⚙ verify).

---

## Phase 7 — Polish + submission  `[AUTO]` / `[HUMAN]`

- **7.1 Architecture diagram** `[AUTO]` — replace the ASCII placeholder with a rendered
  diagram (Mermaid → committed SVG). Required deliverable.
- **7.2 README run instructions + sample config** `[AUTO]` — end-to-end run from clean
  clone; flip the status checklist.
- **7.3 Severity-wording + demo-script review** `[HUMAN]` — your taste pass on tone.
- **7.4 Demo video (<3 min)** `[HUMAN]` — "without AI vs with AI" contrast. Cannot be
  automated.

---

## Open items still genuinely undecided (small, non-blocking)

- **Model:** Sonnet vs Opus for the investigation loop. Default Sonnet for cost/latency;
  bump to Opus only if reasoning quality on the verdict disappoints. Decide after 2.4.
- **Exact threshold / index names** in Phase 5 — settle live over SSH.
