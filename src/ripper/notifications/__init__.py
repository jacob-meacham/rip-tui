"""Notification system for rip-tui.

Public API:
    EventType, NotificationEvent, NotificationDispatcher, create_dispatcher
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ripper.notifications.notifier import (
    EventType,
    NotificationDispatcher,
    NotificationEvent,
)

if TYPE_CHECKING:
    from ripper.config.settings import Settings

logger = logging.getLogger(__name__)

__all__ = [
    "EventType",
    "NotificationDispatcher",
    "NotificationEvent",
    "create_dispatcher",
]


def create_dispatcher(settings: Settings) -> NotificationDispatcher:
    """Build a dispatcher from the current settings."""
    from ripper.notifications.notifier import Notifier

    notifiers: list[Notifier] = []

    if settings.notify_terminal:
        from ripper.notifications.terminal import TerminalNotifier

        notifiers.append(TerminalNotifier())

    if settings.notify_slack_webhook_url:
        from ripper.notifications.slack import SlackNotifier

        notifiers.append(SlackNotifier(settings.notify_slack_webhook_url))

    return NotificationDispatcher(notifiers)
