"""The single elevated capability (SPEC §3.1).

One in-process @tool that can physically only POST Dashboard Studio definitions
into ONE app's `data/ui/views`. The app context comes from env and is baked into
the URL — never from tool arguments — so no argument, however adversarial, can
retarget it. The LLM never sees SPLUNK_WRITE_TOKEN; it can only *invoke* this
tool, and every invocation passes the can_use_tool gate (gate.py) first.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool
from dotenv import load_dotenv

# Conservative on purpose: anything that could change the URL path is illegal.
VIEW_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,99}$")


@dataclass(frozen=True)
class WriteConfig:
    base_url: str  # Splunk management URL, e.g. https://splunk.example.com:8089
    app: str  # the ONE app this capability may write into
    token: str  # write-plane token; distinct from the MCP read token
    web_url: str  # Splunk Web base for human-facing links ("" if unset)
    verify_tls: bool


def write_config() -> WriteConfig:
    load_dotenv()
    return WriteConfig(
        base_url=os.environ["SPLUNK_WRITE_URL"].rstrip("/"),
        app=os.environ["SPLUNK_WRITE_APP"],
        token=os.environ["SPLUNK_WRITE_TOKEN"],
        web_url=os.environ.get("SPLUNK_WEB_URL", "").rstrip("/"),
        verify_tls=os.environ.get("SPLUNK_TLS_VERIFY", "true").lower() != "false",
    )


def validate_view_name(name: Any) -> str:
    if not isinstance(name, str) or not VIEW_NAME_RE.match(name):
        raise ValueError(
            f"illegal view name {str(name)[:80]!r}: must match {VIEW_NAME_RE.pattern}"
        )
    return name


def views_url(cfg: WriteConfig) -> str:
    """The one URL this capability can POST to. Built from config, never from args."""
    return f"{cfg.base_url}/servicesNS/nobody/{cfg.app}/data/ui/views"


def studio_envelope(definition: dict) -> str:
    """Wrap a Dashboard Studio JSON definition in the version-2 XML envelope
    (shape verified live against Splunk 10.2.2 — see
    .claude/session/artifacts/dashboard-studio-recipe.md).

    The JSON rides inside CDATA; a definition containing "]]>" would otherwise
    break out of it, so split the CDATA section (the standard escape). The
    label/description land in the dashboard listing and are XML-escaped.
    """

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    label = esc(str(definition.get("title", "SOC Assist dashboard")))
    description = esc(str(definition.get("description", "")))
    payload = json.dumps(definition).replace("]]>", "]]]]><![CDATA[>")
    return (
        '<dashboard version="2" theme="light">'
        f"<label>{label}</label>"
        f"<description>{description}</description>"
        f"<definition><![CDATA[{payload}]]></definition>"
        "</dashboard>"
    )


def _coerce_definition(definition: Any) -> dict:
    if isinstance(definition, str):  # models sometimes double-encode JSON args
        definition = json.loads(definition)
    if not isinstance(definition, dict):
        raise ValueError("definition must be a JSON object")
    return definition


async def post_dashboard(
    name: str, definition: dict, *, http: httpx.AsyncClient | None = None
) -> str:
    """Create (or update, if it exists) the view. Returns a human-facing URL.

    `http` is injectable so the URL-confinement behavior is testable without a
    live Splunk box.
    """
    cfg = write_config()
    name = validate_view_name(name)
    definition = _coerce_definition(definition)
    url = views_url(cfg)
    data = {"name": name, "eai:data": studio_envelope(definition)}
    headers = {"Authorization": f"Bearer {cfg.token}"}

    async def _do(client: httpx.AsyncClient) -> str:
        resp = await client.post(url, data=data, headers=headers)
        if resp.status_code == 409:  # exists -> update the entity in place
            resp = await client.post(
                f"{url}/{name}", data={"eai:data": data["eai:data"]}, headers=headers
            )
        resp.raise_for_status()
        if cfg.web_url:
            return f"{cfg.web_url}/en-US/app/{cfg.app}/{name}"
        return f"{url}/{name}"

    if http is not None:
        return await _do(http)
    async with httpx.AsyncClient(verify=cfg.verify_tls, timeout=30.0) as client:
        return await _do(client)


@tool(
    "create_dashboard",
    "Create a Splunk Dashboard Studio dashboard presenting investigation evidence. "
    "Writes only into the app configured by the operator; requires human approval.",
    {"name": str, "definition": dict},
)
async def create_dashboard(args: dict[str, Any]) -> dict[str, Any]:
    try:
        view_url = await post_dashboard(args.get("name"), args.get("definition"))
    except Exception as exc:  # the agent gets the reason, never a traceback
        return {
            "content": [{"type": "text", "text": f"Dashboard write failed: {exc}"}],
            "is_error": True,
        }
    return {"content": [{"type": "text", "text": f"Dashboard created: {view_url}"}]}


def create_write_server():
    """The WRITE-plane MCP server: exactly one tool, in-process."""
    return create_sdk_mcp_server(name="soc_write", version="0.1.0", tools=[create_dashboard])
