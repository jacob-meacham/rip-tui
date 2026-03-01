"""Terminal bell notifier — writes BEL character to stderr."""

import sys

from ripper.notifications.notifier import NotificationEvent


class TerminalNotifier:
    """Sends a terminal bell (BEL) on every notification."""

    def send(self, event: NotificationEvent) -> None:
        sys.stderr.write("\a")
        sys.stderr.flush()
