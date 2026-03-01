"""Slack webhook notifier — POST to a webhook URL via stdlib."""

import json
import logging
import urllib.request

from ripper.notifications.notifier import NotificationEvent

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Sends notifications to a Slack incoming webhook."""

    def __init__(self, webhook_url: str) -> None:
        if not webhook_url:
            raise ValueError("Slack webhook URL must not be empty")
        self._webhook_url = webhook_url

    def send(self, event: NotificationEvent) -> None:
        text = f"*[rip]* {event.event_type.value}: {event.message}"
        if event.disc_name:
            text += f" ({event.disc_name})"

        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            self._webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
        except Exception:
            logger.warning(
                "Slack notification failed",
                exc_info=True,
            )
