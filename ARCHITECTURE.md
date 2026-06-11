# Architecture

> **Status: placeholder (Phase 0).** This file holds the text/ASCII architecture
> while the harness is built. A rendered diagram replaces the ASCII block in
> Phase 7 (submission). The required deliverable is "an architecture diagram in
> the repo root showing how it talks to Splunk, how AI is integrated, and the
> data flow" — that lives here.

## The demo arc

`attack sim → Splunk-native detection fires → harness pulls it → Slack ping →
assisted investigation → (approved) dashboard rendered → resolution + summary.`

## Four planes, separated by privilege

```
[Attack sim] → log pipelines (DNS / auth / firewall / …) → Splunk indexes
                                                              │
                          Splunk-native detection (a saved search / alert)
                                                              │ fires
                                                              ▼
                                                  [ Harness — Claude Agent SDK ]
                            ┌─────────────────────────────────┼──────────────────────────────┐
                      READ plane                          WRITE plane                     NOTIFY plane
             Splunk MCP server (read-only):        one in-process @tool:            Slack (Bolt):
             splunk_run_query, splunk_get_indexes  POST Dashboard Studio JSON       notify + investigation
             (SPL explain: Claude-native)          to data/ui/views                 thread. No Splunk
             (mcp__splunk__*)                       (scoped to ONE app)             privilege at all.
                            │                              │
                            │                        canUseTool gate:
                            │                        (a) human approval
                            │                        (b) hard scope check
                            └─────────────────────────────┼──────────────────────────────┘
                                                          ▼
                                        AUDIT plane: agent actions → a Splunk index
                                        (the watcher is watched)
```

- **Read** — native Splunk MCP, read-only, broad. The SDK consumes it as a client.
  Core tools are prefixed `splunk_` (e.g. `splunk_run_query`); the SPL-assistant
  tools are prefixed `saia_` and require *Splunk AI Assistant for SPL* — these are
  **optional**, since the harness defaults to Claude-native SPL explain/generate. Fully
  namespaced for the SDK as `mcp__splunk__<tool>`.
- **Write** — the *only* elevated capability. One narrow `@tool`; the LLM never
  holds Splunk write credentials, it can only invoke the tool, and the tool can
  physically POST to one app's `data/ui/views`.
- **Notify** — Slack; zero Splunk privilege.
- **Audit** — append-only; the agent's own actions become SIEM data.

## Claude Agent SDK mapping (Python, snake_case)

| Need | SDK primitive |
|---|---|
| Connect to the Splunk MCP server | `ClaudeAgentOptions(mcp_servers={"splunk": {"type": "http", "url": ..., "headers": ...}})`; enable with `allowed_tools=["mcp__splunk__*"]` |
| Human-approval gate + hard scope check on writes | `can_use_tool` callback → returns `PermissionResultAllow` / `PermissionResultDeny` |
| Custom Splunk REST write tool | `@tool` → `create_sdk_mcp_server()` → passed into `mcp_servers` |
| Long-running service beside Slack | `query()` / `ClaudeSDKClient` |

See [DESIGN.md](DESIGN.md) for the full design and rationale.
