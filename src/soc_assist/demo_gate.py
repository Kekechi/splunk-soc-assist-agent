"""Gate rejection demo (SPEC §3.4) — the "even a prompt-injected agent can't
exceed scope" proof beat, runnable offline.

Drives the can_use_tool gate and the write tool directly (no live Splunk, no
live agent — HTTP is stubbed) and checks five facts:

  1. read-plane tools pass the gate untouched;
  2. a write smuggling extra targeting args is denied WITHOUT asking a human;
  3. a write with a path-traversal view name is denied WITHOUT asking a human;
  4. an in-scope write the human declines is denied;
  5. an in-scope write the human approves POSTs to the one configured app URL —
     and nowhere else — with the token the LLM never saw.

Run: python -m soc_assist.demo_gate   (exits non-zero on any failure)
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx

from .gate import WRITE_TOOL, make_can_use_tool
from .surface import Surface

# Offline defaults so this runs from a clean clone; a filled .env wins, but no
# request leaves the process either way (MockTransport below).
os.environ.setdefault("SPLUNK_WRITE_URL", "https://splunk.example.com:8089")
os.environ.setdefault("SPLUNK_WRITE_APP", "soc_assist")
os.environ.setdefault("SPLUNK_WRITE_TOKEN", "demo-token-the-llm-never-sees")

from .write_tool import post_dashboard, write_config  # noqa: E402


class ScriptedSurface(Surface):
    """A Surface with pre-scripted approval answers, for the demo."""

    def __init__(self, answers: list[bool]):
        self.answers = answers
        self.approvals_requested = 0

    async def notify(self, text: str, *, debug: bool = False) -> None:
        print(f"  | {text}")

    async def stream(self, text: str) -> None:
        print(f"  | {text}")

    async def request_approval(self, proposal: str) -> bool:
        self.approvals_requested += 1
        answer = self.answers.pop(0)
        print(f"  | [approval requested] -> scripted human says {'YES' if answer else 'NO'}")
        return answer


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    return ok


DEFINITION = {"title": "demo", "dataSources": {}, "visualizations": {}, "layout": {}}


async def main() -> int:
    results: list[bool] = []
    surface = ScriptedSurface(answers=[False, True])
    gate = make_can_use_tool(surface)

    print("\n-- 1. read-plane tool passes --")
    r = await gate("mcp__splunk__splunk_run_query", {"query": "index=_internal"}, None)
    results.append(check("read tool allowed, no approval asked",
                         type(r).__name__ == "PermissionResultAllow"
                         and surface.approvals_requested == 0))

    print("\n-- 2. smuggled targeting argument -> deny, no human asked --")
    r = await gate(WRITE_TOOL, {"name": "x", "definition": DEFINITION, "app": "other_app"}, None)
    results.append(check("denied without approval",
                         type(r).__name__ == "PermissionResultDeny"
                         and surface.approvals_requested == 0,
                         getattr(r, "message", "")[:90]))

    print("\n-- 3. path-traversal view name -> deny, no human asked --")
    r = await gate(WRITE_TOOL, {"name": "../../etc/important", "definition": DEFINITION}, None)
    results.append(check("denied without approval",
                         type(r).__name__ == "PermissionResultDeny"
                         and surface.approvals_requested == 0,
                         getattr(r, "message", "")[:90]))

    print("\n-- 4. in-scope write, human says NO -> deny --")
    r = await gate(WRITE_TOOL, {"name": "soc_demo", "definition": DEFINITION}, None)
    results.append(check("denied after human decline",
                         type(r).__name__ == "PermissionResultDeny"
                         and surface.approvals_requested == 1))

    print("\n-- 5. in-scope write, human says YES -> allow; POST is app-confined --")
    r = await gate(WRITE_TOOL, {"name": "soc_demo", "definition": DEFINITION}, None)
    results.append(check("allowed after human approval",
                         type(r).__name__ == "PermissionResultAllow"
                         and surface.approvals_requested == 2))

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"entry": [{"name": "soc_demo"}]})

    cfg = write_config()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await post_dashboard("soc_demo", DEFINITION, http=http)
    url = str(seen[0].url)
    results.append(check(
        "POST went to the one configured app URL",
        url == f"{cfg.base_url}/servicesNS/nobody/{cfg.app}/data/ui/views", url))
    results.append(check(
        "auth header set from config (never via the LLM)",
        seen[0].headers.get("authorization") == f"Bearer {cfg.token}"))

    print("\n-- 6. tool-layer defense in depth: traversal name rejected pre-HTTP --")
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await post_dashboard("../otherapp", DEFINITION, http=http)
        results.append(check("ValueError raised", False))
    except ValueError as exc:
        results.append(check("ValueError raised", True, str(exc)[:80]))

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
