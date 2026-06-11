"""SlackSurface — the same Surface seam, in a Slack thread (SPEC §4.1).

No inbound server (DESIGN D5): Socket Mode over an outbound websocket, so the
whole NOTIFY plane holds zero Splunk privilege and zero listening ports.

The approval bridge is the one genuinely hard part: `request_approval()` is
awaited deep inside the can_use_tool gate, but the human's button click arrives
on a separate Bolt action handler. They meet through `self._pending` — a
correlation_id -> asyncio.Future map on the one shared event loop. The gate
awaits the Future; the click handler resolves it; a timeout resolves it to
False (default-deny), same as every other ambiguous outcome.

Requires: pip install -e '.[slack]' and SLACK_BOT_TOKEN / SLACK_APP_TOKEN /
SLACK_CHANNEL in the environment (Phase 4.0, a human-provisioned Slack app
with Socket Mode enabled and chat:write).
"""

from __future__ import annotations

import asyncio
import os
import uuid

from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from .surface import Surface

APPROVAL_TIMEOUT_S = 300

_APPROVE = "soc_assist_approve"
_DENY = "soc_assist_deny"


class SlackSurface(Surface):
    """Narrates one investigation as one Slack thread; approvals are buttons."""

    def __init__(self, app: AsyncApp, channel: str):
        self._app = app
        self._channel = channel
        self._thread_ts: str | None = None  # first message roots the thread
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._handler: AsyncSocketModeHandler | None = None
        app.action(_APPROVE)(self._on_action)
        app.action(_DENY)(self._on_action)

    @classmethod
    def from_env(cls) -> "SlackSurface":
        load_dotenv()
        return cls(
            app=AsyncApp(token=os.environ["SLACK_BOT_TOKEN"]),
            channel=os.environ["SLACK_CHANNEL"],
        )

    async def start(self) -> None:
        """Open the Socket Mode connection on the current event loop."""
        load_dotenv()
        self._handler = AsyncSocketModeHandler(self._app, os.environ["SLACK_APP_TOKEN"])
        await self._handler.connect_async()

    async def stop(self) -> None:
        if self._handler is not None:
            await self._handler.close_async()

    # -- Surface ------------------------------------------------------------

    async def notify(self, text: str) -> None:
        await self._post(text)

    async def stream(self, text: str) -> None:
        await self._post(text)

    async def request_approval(self, proposal: str) -> bool:
        correlation_id = uuid.uuid4().hex
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[correlation_id] = future

        await self._app.client.chat_postMessage(
            channel=self._channel,
            thread_ts=self._thread_ts,
            text=f"Approval required:\n{proposal}",  # fallback for notifications
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Approval required*\n{proposal}"},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "style": "primary",
                            "action_id": _APPROVE,
                            "value": correlation_id,
                            "text": {"type": "plain_text", "text": "Approve"},
                        },
                        {
                            "type": "button",
                            "style": "danger",
                            "action_id": _DENY,
                            "value": correlation_id,
                            "text": {"type": "plain_text", "text": "Reject"},
                        },
                    ],
                },
            ],
        )
        try:
            return await asyncio.wait_for(future, APPROVAL_TIMEOUT_S)
        except asyncio.TimeoutError:
            await self._post("No decision within the timeout — denied by default.")
            return False
        finally:
            self._pending.pop(correlation_id, None)

    # -- internals ------------------------------------------------------------

    async def _post(self, text: str) -> None:
        resp = await self._app.client.chat_postMessage(
            channel=self._channel, thread_ts=self._thread_ts, text=text
        )
        if self._thread_ts is None:
            self._thread_ts = resp["ts"]

    async def _on_action(self, ack, body, client) -> None:
        await ack()
        action = body["actions"][0]
        future = self._pending.get(action["value"])
        if future is not None and not future.done():
            future.set_result(action["action_id"] == _APPROVE)
        # Freeze the message so the buttons can't be clicked twice.
        decided = "Approved" if action["action_id"] == _APPROVE else "Rejected"
        await client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            text=f"{decided} by <@{body['user']['id']}>",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{decided}* by <@{body['user']['id']}>",
                    },
                }
            ],
        )
