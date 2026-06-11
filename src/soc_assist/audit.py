"""AUDIT plane (SPEC §6.1) — the watcher is watched.

Every gate decision and every write lands as a structured JSON event in a
dedicated Splunk index over HEC, so the agent that investigates the SIEM is
observable inside that same SIEM. Auditing must never take the investigation
down with it: on any emit failure we warn loudly on stderr and continue.

Unconfigured (no SPLUNK_AUDIT_* env) it degrades to a stderr stub so local
runs still show every decision — visibly, not silently dropped.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import httpx
from dotenv import load_dotenv

SOURCETYPE = "soc_assist:audit"


class AuditEmitter:
    """Appends agent-action events to the audit index via HEC."""

    def __init__(self, hec_url: str, token: str, index: str, verify_tls: bool = True):
        self._endpoint = f"{hec_url.rstrip('/')}/services/collector/event"
        self._token = token
        self._index = index
        self._verify = verify_tls

    @classmethod
    def from_env(cls) -> "AuditEmitter | None":
        """The configured emitter, or None when the audit plane isn't set up."""
        load_dotenv()
        url = os.environ.get("SPLUNK_AUDIT_HEC_URL", "")
        token = os.environ.get("SPLUNK_AUDIT_HEC_TOKEN", "")
        if not (url and token):
            return None
        return cls(
            hec_url=url,
            token=token,
            index=os.environ.get("SPLUNK_AUDIT_INDEX", "soc_audit"),
            verify_tls=os.environ.get("SPLUNK_TLS_VERIFY", "true").lower() != "false",
        )

    async def emit(
        self,
        action: str,
        *,
        tool: str = "",
        args_summary: str = "",
        decision: str = "",
        detail: Any = None,
    ) -> None:
        event = {
            "action": action,
            "tool": tool,
            "args_summary": args_summary[:500],
            "decision": decision,
            "detail": detail,
            "actor": "soc-assist-agent",
        }
        payload = {
            "time": time.time(),
            "index": self._index,
            "sourcetype": SOURCETYPE,
            "event": event,
        }
        try:
            async with httpx.AsyncClient(verify=self._verify, timeout=10.0) as client:
                resp = await client.post(
                    self._endpoint,
                    content=json.dumps(payload),
                    headers={"Authorization": f"Splunk {self._token}"},
                )
                resp.raise_for_status()
        except Exception as exc:
            print(f"[audit] EMIT FAILED ({exc}) — event was: {event}", file=sys.stderr)


class StderrAuditEmitter(AuditEmitter):
    """Local stub: same interface, events go to stderr (SPEC §6.1 done-criterion)."""

    def __init__(self):  # no HEC config on purpose
        pass

    async def emit(self, action: str, **kw: Any) -> None:
        print(f"[audit] {action} {json.dumps(kw, default=str)}", file=sys.stderr)


def emitter() -> AuditEmitter:
    """The configured emitter, falling back to the visible stderr stub."""
    return AuditEmitter.from_env() or StderrAuditEmitter()
