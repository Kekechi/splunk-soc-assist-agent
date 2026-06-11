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
    """Where the investigation is narrated and approvals are requested."""

    @abstractmethod
    async def notify(self, text: str) -> None:
        """Post a discrete status line (tool call, decision, error)."""

    @abstractmethod
    async def stream(self, text: str) -> None:
        """Post incremental investigation output as the agent produces it."""

    @abstractmethod
    async def request_approval(self, proposal: str) -> bool:
        """Ask the human to approve a proposed action. Anything but an explicit
        yes — including timeout, EOF, or ambiguity — must return False."""


class CliSurface(Surface):
    """Terminal backend: stdout for output, stdin y/n for approvals."""

    async def notify(self, text: str) -> None:
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
