"""Phase 1 spike — prove the Splunk read path, then stop.

Goal: confirm the Claude Agent SDK can connect to the Splunk MCP server, run ONE
search, and print real results. This de-risks the entire architecture —
everything downstream assumes this link works.

Make this green before writing anything else.

Run:
    cp .env.example .env        # then fill in the SPLUNK_MCP_* values
    pip install -e .
    python spike.py

If it fights you, it's almost always one of two things, and the output below
tells you which:
  * The `init` line reports each MCP server's connection status. Anything other
    than "connected" => the endpoint URL, the transport type, or the auth header.
  * Tool names are namespaced `mcp__splunk__<tool>`. The real Splunk MCP tools
    are prefixed `splunk_` (e.g. `splunk_run_query`), so the wildcard
    `mcp__splunk__*` below is what lets the agent actually see them. A bare
    `mcp__splunk__run_query` would match nothing and the agent would do nothing.
"""

import asyncio
import os

from dotenv import load_dotenv
from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    SystemMessage,
    AssistantMessage,
    ResultMessage,
)

load_dotenv()

MCP_URL = os.environ["SPLUNK_MCP_URL"]
MCP_TRANSPORT = os.environ.get("SPLUNK_MCP_TRANSPORT", "http")  # "http" or "sse"
AUTH_SCHEME = os.environ.get("SPLUNK_MCP_AUTH_SCHEME", "Bearer")
TOKEN = os.environ.get("SPLUNK_MCP_TOKEN", "")

headers: dict[str, str] = {}
if TOKEN:
    headers["Authorization"] = f"{AUTH_SCHEME} {TOKEN}".strip()

splunk_server = {
    "type": MCP_TRANSPORT,
    "url": MCP_URL,
    "headers": headers,
}

options = ClaudeAgentOptions(
    mcp_servers={"splunk": splunk_server},
    # Wildcard so the agent sees every splunk_* / saia_* tool. The spike only
    # needs splunk_run_query; tighten this to an explicit allow-list later.
    allowed_tools=["mcp__splunk__*"],
)

PROMPT = (
    "Use the Splunk tools to run this exact search: `index=_internal | head 5`. "
    "Then tell me, in one or two plain sentences, what the results contain."
)


async def main() -> None:
    print(f"-> Connecting to Splunk MCP at {MCP_URL} (transport={MCP_TRANSPORT})\n")
    async for message in query(prompt=PROMPT, options=options):
        # 1) Connection status — the fastest way to diagnose auth/endpoint issues.
        if isinstance(message, SystemMessage) and message.subtype == "init":
            servers = message.data.get("mcp_servers", [])
            print("MCP servers:", servers)
            # At init, servers report "pending" — the connection resolves after
            # this message, so only "failed" is a real problem here. The true
            # success signal is the tool call + result below.
            failed = [s for s in servers if s.get("status") == "failed"]
            if failed:
                print("[!] Failed to connect:", failed)
                print("    Check SPLUNK_MCP_URL, SPLUNK_MCP_TRANSPORT, and the auth header.")

        # 2) Show each MCP tool the agent actually calls.
        if isinstance(message, AssistantMessage):
            for block in message.content:
                name = getattr(block, "name", "")
                if isinstance(name, str) and name.startswith("mcp__"):
                    print("[tool]", name)

        # 3) The final answer.
        if isinstance(message, ResultMessage) and message.subtype == "success":
            print("\n[OK] Result:\n", message.result)


if __name__ == "__main__":
    asyncio.run(main())
