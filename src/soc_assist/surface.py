"""Surface — the notify/approve seam (SPEC §2.6).

The investigation loop and the write gate depend only on this interface, never on a
concrete backend. `CliSurface` runs the whole app from a terminal; `SlackSurface`
(Phase 4) implements the same interface behind the async approval bridge. Swapping
them is the only wiring change.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class Surface(ABC):
    """Where the investigation is narrated and approvals are requested.

    Two audiences, one seam. The *analyst* sees only signal — the detection that
    fired, the verdict, the approval request, the published dashboard. The
    *operator* sees the verbose trail — every tool call, SPL query, and the
    agent's running narration. `notify(text)` and `request_approval()` are
    analyst-facing; `notify(text, debug=True)` and `stream()` are operator-facing.
    A single-stream backend (the CLI) shows both; a split backend (Slack) can
    route them to separate channels.
    """

    @abstractmethod
    async def notify(self, text: str, *, debug: bool = False) -> None:
        """Post a discrete status line. `debug=True` marks it operator-only
        noise (tool calls, status); the default is an analyst-facing signal."""

    @abstractmethod
    async def stream(self, text: str) -> None:
        """Post the agent's incremental narration (operator-facing detail)."""

    @abstractmethod
    async def request_approval(self, proposal: str) -> bool:
        """Ask the human to approve a proposed action. Anything but an explicit
        yes — including timeout, EOF, or ambiguity — must return False."""


class CliSurface(Surface):
    """Terminal backend: stdout for output, stdin y/n for approvals. One stream,
    so the operator/analyst split collapses — everything prints."""

    async def notify(self, text: str, *, debug: bool = False) -> None:
        print(text, flush=True)

    async def stream(self, text: str) -> None:
        print(text, flush=True)

    async def request_approval(self, proposal: str) -> bool:
        print(f"\n=== APPROVAL REQUIRED ===\n{proposal}", flush=True)
        try:
            answer = await asyncio.to_thread(input, "Approve? [y/N] ")
        except EOFError:  # non-interactive stdin -> default-deny
            print("(no interactive stdin — denying by default)", flush=True)
            return False
        return answer.strip().lower() in ("y", "yes")
