"""Configuration — all of it env vars, grouped by privilege plane (DESIGN.md §1).

READ plane only for now; WRITE/NOTIFY/AUDIT config lands with their phases.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv


def splunk_mcp_server() -> dict:
    """The read-only Splunk MCP server definition for ClaudeAgentOptions.mcp_servers."""
    load_dotenv()  # idempotent; callers don't have to remember it
    url = os.environ["SPLUNK_MCP_URL"]
    transport = os.environ.get("SPLUNK_MCP_TRANSPORT", "http")  # "http" or "sse"
    scheme = os.environ.get("SPLUNK_MCP_AUTH_SCHEME", "Bearer")
    token = os.environ.get("SPLUNK_MCP_TOKEN", "")
    headers = {"Authorization": f"{scheme} {token}".strip()} if token else {}
    return {"type": transport, "url": url, "headers": headers}


def investigation_model() -> str:
    """Sonnet by default for cost/latency (SPEC open item); override via env."""
    load_dotenv()
    return os.environ.get("SOC_ASSIST_MODEL", "sonnet")
