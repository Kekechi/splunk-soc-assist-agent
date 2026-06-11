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

from .alert import BRUTE_FORCE_FIXTURE, AlertContext
from .config import investigation_model, splunk_mcp_server
from .dashboard import build_brute_force_dashboard
from .gate import make_can_use_tool
from .investigate import Investigation, investigate
from .prompts import build_system_prompt
from .surface import CliSurface, Surface
from .write_tool import create_write_server

DASHBOARD_TURN = """Now publish the evidence dashboard. Call create_dashboard with:
- name: "{name}"
- definition: exactly the JSON object below, unmodified.

{definition_json}

A human reviews every write; if it is denied, accept that, summarize in two sentences
what the dashboard would have shown, and stop. If it succeeds, give the analyst the
dashboard URL and one line on what each of the three panels shows."""


def _full_options(surface: Surface) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        mcp_servers={
            "splunk": splunk_mcp_server(),  # READ plane (external, read-only)
            "soc_write": create_write_server(),  # WRITE plane (in-process, one tool)
        },
        allowed_tools=["mcp__splunk__*"],  # reads pre-approved; the write is NOT
        system_prompt=build_system_prompt(can_write=True),
        setting_sources=[],
        can_use_tool=make_can_use_tool(surface),
        model=investigation_model(),
        max_turns=50,
    )


async def run(alert: AlertContext, surface: Surface) -> Investigation:
    async with ClaudeSDKClient(options=_full_options(surface)) as client:
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

    alert = BRUTE_FORCE_FIXTURE
    try:
        await surface.notify(f"=== soc-assist | investigating: {alert.rule_name} ===\n")
        await run(alert, surface)
    finally:
        if use_slack:
            await surface.stop()


if __name__ == "__main__":
    asyncio.run(main())
