"""The full soc-assist console run (Phase 3 end-to-end).

One gated agent session: investigate the alert read-only, return the verdict,
then publish the evidence dashboard through the single write tool — which the
can_use_tool gate scope-checks and a human approves, every time.

Wiring note (load-bearing): the write tool is deliberately NOT in
allowed_tools. Allowed tools are auto-approved and would bypass can_use_tool;
leaving it out is what routes every write through the gate.

Run: python -m soc_assist.run
"""

from __future__ import annotations

import asyncio
import json
import sys

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
    ToolUseBlock,
)

from .alert import (
    BRUTE_FORCE_FIXTURE,
    DETECTION_SAVED_SEARCH,
    AlertContext,
    alert_from_detection_row,
)
from .audit import emitter
from .config import investigation_model, splunk_mcp_server
from .dashboard import build_brute_force_dashboard
from .gate import make_can_use_tool
from .investigate import Investigation, _response_text, investigate
from .prompts import build_system_prompt, parse_verdict
from .surface import CliSurface, Surface
from .write_tool import create_write_server

DASHBOARD_TURN = """Now publish the evidence dashboard. Call create_dashboard with:
- name: "{name}"
- definition: exactly the JSON object below, unmodified.

{definition_json}

A human reviews every write; if it is denied, accept that, summarize in two sentences
what the dashboard would have shown, and stop. If it succeeds, give the analyst the
dashboard URL and one line on what each of the three panels shows."""


LIVE_ALERT_TURN = f"""A detection may have fired. Run the saved search
"{DETECTION_SAVED_SEARCH}" (app context: soc_assist) with your Splunk tools and reply
with ONLY a JSON object for the single fired row, keys exactly as the search returns
them: src_ip, user, failure_count, actions, earliest_time, latest_time.
If it returns no rows, reply with exactly {{}}."""


async def fetch_live_alert(client: ClaudeSDKClient) -> AlertContext | None:
    """Bootstrap the alert from the real provisioned detection, via the agent's
    own read plane (the MCP token is not a raw splunkd credential, and the
    harness holds no other read credential — by design)."""
    await client.query(LIVE_ALERT_TURN)
    row = parse_verdict(await _response_text(client))
    return alert_from_detection_row(row) if row else None


def _full_options(surface: Surface) -> ClaudeAgentOptions:
    audit = emitter()

    async def on_decision(tool_name: str, decision: str, reason: str) -> None:
        await audit.emit(
            "gate_decision", tool=tool_name, decision=decision, args_summary=reason
        )

    return ClaudeAgentOptions(
        mcp_servers={
            "splunk": splunk_mcp_server(),  # READ plane (external, read-only)
            "soc_write": create_write_server(),  # WRITE plane (in-process, one tool)
        },
        # Nothing is pre-approved: pre-allowed tools bypass can_use_tool, so an
        # empty list is what routes EVERY tool call — reads included — through
        # the gate, and from there into the audit index (SPEC 6.1 "every
        # allow/deny"). Tool visibility comes from MCP attachment, not from
        # allowed_tools (verified live in Phase 3).
        allowed_tools=[],
        system_prompt=build_system_prompt(can_write=True),
        setting_sources=[],
        can_use_tool=make_can_use_tool(surface, on_decision=on_decision),
        model=investigation_model(),
        max_turns=50,
    )


async def run(alert: AlertContext | None, surface: Surface) -> Investigation | None:
    """Investigate `alert` (or, if None, the live fired detection) and publish
    the evidence dashboard through the gate."""
    async with ClaudeSDKClient(options=_full_options(surface)) as client:
        if alert is None:
            await surface.notify("[live] dispatching the provisioned detection ...")
            alert = await fetch_live_alert(client)
            if alert is None:
                await surface.notify("[live] detection returned no rows — nothing to do.")
                return None
            await surface.notify(
                f"[live] fired: {alert.rule_name} | {alert.entities} "
                f"| {alert.observed_count} failures"
            )
        inv = await investigate(alert, surface, debug=True, client=client)
        await _print_verdict(inv, surface)

        definition = build_brute_force_dashboard(inv, alert)
        view_name = alert.rule_id.replace("-", "_")
        await client.query(
            DASHBOARD_TURN.format(name=view_name, definition_json=json.dumps(definition))
        )
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        await surface.stream(block.text)
                    elif isinstance(block, ToolUseBlock):
                        await surface.notify(f"  [tool] {block.name}")
    return inv


async def _print_verdict(inv: Investigation, surface: Surface) -> None:
    steps = "\n".join(f"  {i}. {s}" for i, s in enumerate(inv.recommended_next_steps, 1))
    queries = "\n".join(f"  - {q}" for q in inv.queries_run) or "  (none)"
    await surface.notify(
        f"""
=== Investigation ===
severity: {inv.severity}
reason:   {inv.severity_reason}

summary:
{inv.summary}

queries run ({len(inv.queries_run)}):
{queries}

recommended next steps:
{steps}
"""
    )


async def main() -> None:
    use_slack = "slack" in sys.argv[1:]
    if use_slack:
        from .slack_app import SlackSurface  # optional dep: pip install -e '.[slack]'

        surface: Surface = SlackSurface.from_env()
        await surface.start()
    else:
        surface = CliSurface()

    alert = None if "--live" in sys.argv[1:] else BRUTE_FORCE_FIXTURE
    try:
        await surface.notify("=== soc-assist ===\n")
        await run(alert, surface)
    finally:
        if use_slack:
            await surface.stop()


if __name__ == "__main__":
    asyncio.run(main())
