"""Core notification abstractions: events, protocol, and dispatcher."""

import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Types of notification events."""

    ACTION_NEEDED = "Action Needed"
    INSERT_DISC = "Insert Disc"
    RIP_COMPLETE = "Rip Complete"
    RIP_FAILED = "Rip Failed"


@dataclass(frozen=True)
class NotificationEvent:
    """Immutable notification payload."""

    event_type: EventType
    message: str
    disc_name: str = ""


class Notifier(Protocol):
    """Protocol for notification channels."""

    def send(self, event: NotificationEvent) -> None: ...


class NotificationDispatcher:
    """Fan-out dispatcher that sends events to all registered notifiers.

    Each send runs in a daemon thread so it never blocks the TUI.
    Exceptions in individual notifiers are logged, never raised.
    """

    def __init__(self, notifiers: list[Notifier]) -> None:
        self._notifiers = list(notifiers)

    @property
    def enabled(self) -> bool:
        """True if at least one notifier is registered."""
        return len(self._notifiers) > 0

    def notify(self, event: NotificationEvent) -> None:
        """Send event to all notifiers in daemon threads."""
        for notifier in self._notifiers:
            thread = threading.Thread(
                target=self._safe_send,
                args=(notifier, event),
                daemon=True,
            )
            thread.start()

    @staticmethod
    def _safe_send(
        notifier: Notifier, event: NotificationEvent,
    ) -> None:
        """Send with exception guard."""
        try:
            notifier.send(event)
        except Exception:
            logger.warning(
                "Notifier %s failed", type(notifier).__name__,
                exc_info=True,
            )
