# Architecture

> The required deliverable — "an architecture diagram showing how it talks to
> Splunk, how AI is integrated, and the data flow" — lives here:
> [`architecture.svg`](architecture.svg) (rendered from
> [`architecture.mmd`](architecture.mmd), also shown inline below).

## The demo arc

```bash
scripts/load_sample.sh              # attack sim → index=auth
printf 'y\n' | python -m soc_assist.run --live
# detection fires → agent investigates (read-only) → rubric-cited verdict
# → gated, human-approved dashboard publish → URL + audit trail in Splunk
```

## Four planes, separated by privilege

![architecture](architecture.svg)

```mermaid
flowchart TB
    subgraph SPLUNK["Splunk Enterprise (10.2.x)"]
        IDX[("index=auth<br/>sample brute-force data")]
        DET["detection: saved search<br/>soc_assist_bf_detect"]
        DASH["evidence dashboard<br/>(Dashboard Studio v2, app soc_assist)"]
        AIDX[("index=soc_audit<br/>agent action log")]
        IDX --> DET
    end

    LOADER["scripts/load_sample.sh<br/>(attack sim: 51 failed SSH logins, then 1 success)"] --> IDX

    subgraph HARNESS["Harness — Claude Agent SDK (Python)"]
        AGENT["Claude agent<br/>senior-analyst persona + severity rubric<br/>(SPL explained Claude-native; saia_* optional)"]
        GATE{"can_use_tool gate<br/>1. hard scope check — out-of-scope: denied, no human asked<br/>2. human approval — default-deny"}
        TOOL["WRITE plane: one in-process @tool create_dashboard<br/>URL fixed to ONE app from env; LLM never sees the token"]
        AGENT -- "every tool call" --> GATE
        GATE -- "approved + in-scope only" --> TOOL
    end

    subgraph NOTIFY["NOTIFY plane — zero Splunk privilege"]
        SURF["Surface seam<br/>CliSurface (terminal) | SlackSurface (Socket Mode,<br/>Approve/Reject buttons → asyncio.Future bridge)"]
    end

    MCP["READ plane — Splunk MCP server<br/>read-only, broad: mcp__splunk__*"]

    DET -- "fired alert → AlertContext" --> AGENT
    AGENT <-- "investigative SPL" --> MCP
    MCP <--> SPLUNK
    GATE <-- "request_approval(proposal)" --> SURF
    TOOL -- "POST data/ui/views (least-priv token)" --> DASH
    GATE -. "every decision (allow/deny + reason)" .-> HEC
    TOOL -. "every write (success/error)" .-> HEC
    HEC["AUDIT plane — HEC emitter"] --> AIDX

    ANALYST(["junior analyst"]) <--> SURF
    ANALYST -- "reads dashboard + audit trail in Splunk Web" --> DASH
```

- **READ** — native Splunk MCP, read-only, broad. The SDK consumes it as an external
  MCP client. Core tools are prefixed `splunk_` (e.g. `splunk_run_query`); the
  SPL-assistant `saia_*` tools are **optional** — the harness explains SPL
  Claude-native. Namespaced as `mcp__splunk__<tool>`, allowed via the
  `mcp__splunk__*` wildcard.
- **WRITE** — the *only* elevated capability. One in-process `@tool`
  (`create_dashboard`); the LLM never holds Splunk write credentials, it can only
  *invoke* the tool, and the tool's URL is built from operator config so it can
  physically POST to one app's `data/ui/views` and nowhere else. The deployment
  pairs it with a dedicated Splunk role that has **zero capabilities** and only
  that app's write ACL.
- **NOTIFY** — the `Surface` seam; Slack (Socket Mode — no inbound server) or the
  terminal. Zero Splunk privilege.
- **AUDIT** — every gate decision and every write, appended to a dedicated index
  over HEC; surfaced in its own dashboard. The watcher is watched.

### The gate (the security core)

Every tool call passes `can_use_tool`. Reads pass. For the write tool, **scope
beats approval**: arguments that try to retarget the write (extra args, path
traversal in the view name) are denied *before any human is asked*, so a
persuaded human cannot bless an escape; in-scope writes still require an explicit
yes, with anything else — timeout, EOF, ambiguity — denied by default. Even a
fully prompt-injected agent cannot exceed the one-app scope.
`python -m soc_assist.demo_gate` is the runnable proof.

## Claude Agent SDK mapping (Python, snake_case)

| Need | SDK primitive |
|---|---|
| Connect to the Splunk MCP server | `ClaudeAgentOptions(mcp_servers={"splunk": {"type": "http", "url": ..., "headers": ...}})` + `allowed_tools=["mcp__splunk__*"]` |
| Human-approval gate + hard scope check on writes | `can_use_tool` callback → `PermissionResultAllow` / `PermissionResultDeny`. The write tool is deliberately **not** in `allowed_tools` — pre-allowed tools bypass the callback |
| Custom Splunk REST write tool | `@tool` → `create_sdk_mcp_server()` → passed into `mcp_servers` |
| Multi-turn investigation + structured verdict | `ClaudeSDKClient` (investigate → JSON verdict → gated dashboard turn, one session) |
| No ambient config leakage | `setting_sources=[]` |

See [DESIGN.md](DESIGN.md) for the full design and rationale, and
[SPEC.md](SPEC.md) for the build ledger.
