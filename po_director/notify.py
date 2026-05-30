"""Slack notify helper — the Director's gateway (replaces nanoc's gateway).

Deterministic side-effect helper: the *flow* calls this to guarantee a proposal
or reflection reaches Slack, regardless of what the agent did in its turn. Uses
`SLACK_BOT_TOKEN` + `chat.postMessage` over stdlib `urllib` (no extra deps).

No-op (returns False, logs) when the channel is unset or the token is missing —
posting is opt-in per workspace (`slack_channel` defaults to None).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_SLACK_URL = "https://slack.com/api/chat.postMessage"


def post_slack(
    channel: str | None,
    title: str,
    body: str,
    *,
    token: str | None = None,
) -> bool:
    """Post `*title*\\n body` to a Slack channel. Returns True on success.

    Returns False (no exception) when posting is not configured or fails, so a
    pulse never crashes on a notify problem.
    """
    if not channel:
        logger.info("slack post skipped: no channel configured")
        return False
    bot_token = token or os.environ.get("SLACK_BOT_TOKEN")
    if not bot_token:
        logger.warning("slack post skipped: SLACK_BOT_TOKEN not set")
        return False

    text = "*" + title + "*\n" + body if title else body
    payload = json.dumps({"channel": channel, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        _SLACK_URL,
        data=payload,
        headers={
            "Authorization": "Bearer " + bot_token,
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        logger.exception("slack post failed for channel %s", channel)
        return False

    if not data.get("ok"):
        logger.warning("slack post rejected for channel %s: %s", channel, data.get("error"))
        return False
    return True
