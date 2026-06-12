"""Phase 2 — the console investigation loop (SPEC §2.4, §2.5).

Hard-coded alert context in, structured plain-language verdict out. Read-only:
the agent sees nothing but the Splunk MCP tools, and `setting_sources=[]` keeps
ambient ~/.claude config (and its MCP servers) out of the session.

Run: python -m soc_assist.investigate
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

from .alert import BRUTE_FORCE_FIXTURE, AlertContext
from .config import investigation_model, splunk_mcp_server
from .profile import load_profile
from .prompts import (
    VERDICT_REQUEST,
    VERDICT_RETRY,
    build_system_prompt,
    format_alert,
    parse_verdict,
)
from .surface import CliSurface, Surface


@dataclass
class Investigation:
    """The structured outcome of one alert investigation."""

    summary: str
    severity: str
    severity_reason: str
    queries_run: list[str]
    recommended_next_steps: list[str]


def _options() -> ClaudeAgentOptions:
    profile = load_profile()
    return ClaudeAgentOptions(
        mcp_servers={"splunk": splunk_mcp_server()},
        # Wildcard is load-bearing: a bare mcp__splunk__run_query matches nothing.
        allowed_tools=["mcp__splunk__*"],
        system_prompt=build_system_prompt(pipeline_profile=profile.pipeline_profile),
        # SPEC §2.5: don't inherit ~/.claude settings (ambient MCP servers leak in
        # otherwise — observed in the Phase 1 spike).
        setting_sources=[],
        model=investigation_model(),
        max_turns=40,
    )


def _spl_of(block: ToolUseBlock) -> str | None:
    """The SPL string of a Splunk query tool call, or None for other tools."""
    if not block.name.endswith("splunk_run_query"):
        return None
    inp = block.input or {}
    for key in ("query", "search", "spl", "search_query"):
        if key in inp:
            return str(inp[key])
    return json.dumps(inp)


async def investigate(
    alert: AlertContext,
    surface: Surface,
    *,
    debug: bool = False,
    client: ClaudeSDKClient | None = None,
) -> Investigation:
    """Run the investigation for one alert and return its verdict.

    With no `client`, opens a fresh read-only session (Phase 2 behavior). The
    full app (run.py) passes its own gated session instead so the conversation
    can continue into the dashboard step.
    """
    if client is None:
        async with ClaudeSDKClient(options=_options()) as own:
            return await investigate(alert, surface, debug=debug, client=own)

    queries_run: list[str] = []

    await client.query(format_alert(alert))
    async for message in client.receive_response():
        if debug and isinstance(message, SystemMessage) and message.subtype == "init":
            await surface.notify(
                f"[debug] MCP servers: {message.data.get('mcp_servers', [])}",
                debug=True,
            )
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    await surface.stream(block.text)
                elif isinstance(block, ToolUseBlock):
                    spl = _spl_of(block)
                    if spl is not None:
                        queries_run.append(spl)
                        await surface.notify(f"  [splunk query] {spl}", debug=True)
                    else:
                        await surface.notify(f"  [tool] {block.name}", debug=True)

    # Structured tail: one more turn that must come back as bare JSON.
    await client.query(VERDICT_REQUEST)
    text = await _response_text(client)
    try:
        verdict = parse_verdict(text)
    except (ValueError, json.JSONDecodeError):
        await surface.notify("[!] Verdict was not valid JSON — asking once more.", debug=True)
        await client.query(VERDICT_RETRY)
        verdict = parse_verdict(await _response_text(client))

    return Investigation(
        summary=verdict.get("summary", ""),
        severity=verdict.get("severity", "unknown"),
        severity_reason=verdict.get("severity_reason", ""),
        queries_run=queries_run,
        recommended_next_steps=list(verdict.get("recommended_next_steps", [])),
    )


async def _response_text(client: ClaudeSDKClient) -> str:
    parts: list[str] = []
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
    return "".join(parts)


async def main() -> None:
    surface = CliSurface()
    alert = BRUTE_FORCE_FIXTURE
    await surface.notify(f"=== soc-assist | investigating: {alert.rule_name} ===\n")

    inv = await investigate(alert, surface, debug=True)

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

    # SPEC §2.6 done-criterion: the approval seam works end-to-end on the CLI.
    # Phase 3 wires this same call into the can_use_tool gate.
    approved = await surface.request_approval(
        "Build a Splunk evidence dashboard for this investigation?\n"
        "(Phase 3 connects this approval to the gated write tool.)"
    )
    await surface.notify(f"approval decision: {'approved' if approved else 'denied'}")


if __name__ == "__main__":
    asyncio.run(main())
