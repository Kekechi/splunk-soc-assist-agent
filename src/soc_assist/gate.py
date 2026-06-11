"""The can_use_tool gate (SPEC §3.3) — the security core.

Two independent layers on every write, in this order:

(a) **hard scope check** — the call's arguments must be unable to point anywhere
    but the one allowed app. An out-of-scope target is denied *before any human
    is asked*: scope beats approval, so a persuaded human can't bless an escape.
(b) **human approval** — through the Surface seam (CLI now, Slack later; the
    gate never knows which). Default-deny on anything but an explicit yes.

Read tools pass straight through. Treat any weakening of this module as a
regression (CLAUDE.md).
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from .surface import Surface

WRITE_TOOL = "mcp__soc_write__create_dashboard"

# The write tool accepts exactly these arguments. Anything else — app, owner,
# url, whatever a prompt-injected agent invents — is a retargeting attempt.
_ALLOWED_ARGS = {"name", "definition"}

# Decision-audit hook (Phase 6 plugs the HEC emitter in here).
OnDecision = Callable[[str, str, str], Awaitable[None]]


def scope_violation(input_data: dict[str, Any]) -> str | None:
    """Why this write call is out of scope, or None if it is in scope."""
    unexpected = set(input_data) - _ALLOWED_ARGS
    if unexpected:
        return f"unexpected argument(s) {sorted(unexpected)} — the write target is fixed by configuration"

    name = input_data.get("name")
    if not isinstance(name, str):
        return "view name must be a string"
    # Import here keeps gate.py free of httpx at import time (write extra).
    from .write_tool import VIEW_NAME_RE

    if not VIEW_NAME_RE.match(name):
        return (
            f"illegal view name {name[:80]!r} — must match {VIEW_NAME_RE.pattern} "
            "(no path separators, no namespace escapes)"
        )

    definition = input_data.get("definition")
    if isinstance(definition, str):
        try:
            definition = json.loads(definition)
        except json.JSONDecodeError:
            return "definition is not valid JSON"
    if not isinstance(definition, dict):
        return "definition must be a JSON object"
    return None


def _proposal(input_data: dict[str, Any]) -> str:
    name = input_data.get("name", "?")
    definition = input_data.get("definition") or {}
    if isinstance(definition, str):
        try:
            definition = json.loads(definition)
        except json.JSONDecodeError:
            definition = {}
    title = definition.get("title", "(untitled)")
    panels = len(definition.get("visualizations", {})) or "?"
    return (
        "The agent wants to create a Splunk dashboard:\n"
        f"  view name : {name}\n"
        f"  title     : {title}\n"
        f"  panels    : {panels}\n"
        "It will be written into the operator-configured app only."
    )


def make_can_use_tool(surface: Surface, on_decision: OnDecision | None = None):
    """Build the can_use_tool callback bound to a Surface (and audit hook)."""

    async def _record(tool_name: str, decision: str, reason: str) -> None:
        if on_decision is not None:
            await on_decision(tool_name, decision, reason)

    async def can_use_tool(tool_name: str, input_data: dict[str, Any], context: Any):
        # Everything that is not the write tool is the read plane: allow.
        if tool_name != WRITE_TOOL:
            await _record(tool_name, "allow", "read-plane tool")
            return PermissionResultAllow()

        # (a) Hard scope check — never ask a human about an out-of-scope write.
        reason = scope_violation(input_data)
        if reason is not None:
            await _record(tool_name, "deny", f"out of scope: {reason}")
            await surface.notify(f"[gate] DENIED without approval — {reason}")
            return PermissionResultDeny(message=f"Out of scope: {reason}")

        # (b) Human approval — default-deny.
        approved = await surface.request_approval(_proposal(input_data))
        if not approved:
            await _record(tool_name, "deny", "human declined")
            return PermissionResultDeny(message="A human reviewed this write and declined it.")

        await _record(tool_name, "allow", "in scope + human approved")
        return PermissionResultAllow()

    return can_use_tool
