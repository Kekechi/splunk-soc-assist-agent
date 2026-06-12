"""Configuration — endpoints/secrets as env vars, grouped by privilege plane
(DESIGN.md §1). The WRITE/AUDIT planes read their own env in write_tool.py /
audit.py; *what to detect and where* lives in the deployment profile (profile.py),
re-exported here as `load_profile` for discoverability.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from .profile import load_profile  # noqa: F401  (re-exported; see module docstring)


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
