"""Tests for the notification system."""

import json
import threading
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import pytest

from ripper.notifications import (
    EventType,
    NotificationDispatcher,
    NotificationEvent,
    create_dispatcher,
)
from ripper.notifications.slack import SlackNotifier
from ripper.notifications.terminal import TerminalNotifier

# ── NotificationEvent ────────────────────────────────────────────────


def test_event_is_frozen():
    event = NotificationEvent(
        event_type=EventType.ACTION_NEEDED,
        message="test",
    )
    with pytest.raises(FrozenInstanceError):
        event.message = "changed"  # type: ignore[misc]


def test_event_defaults():
    event = NotificationEvent(
        event_type=EventType.RIP_COMPLETE,
        message="done",
    )
    assert event.disc_name == ""


# ── NotificationDispatcher ───────────────────────────────────────────


def test_dispatcher_fans_out_to_all_notifiers():
    notifier_a = MagicMock()
    notifier_b = MagicMock()
    dispatcher = NotificationDispatcher([notifier_a, notifier_b])

    event = NotificationEvent(
        event_type=EventType.ACTION_NEEDED,
        message="choose mode",
    )
    dispatcher.notify(event)

    # Wait for daemon threads to finish
    for t in threading.enumerate():
        if t.daemon and t.is_alive():
            t.join(timeout=2)

    notifier_a.send.assert_called_once_with(event)
    notifier_b.send.assert_called_once_with(event)


def test_failing_notifier_does_not_block_others():
    failing = MagicMock()
    failing.send.side_effect = RuntimeError("boom")
    working = MagicMock()
    dispatcher = NotificationDispatcher([failing, working])

    event = NotificationEvent(
        event_type=EventType.RIP_FAILED,
        message="oops",
    )
    dispatcher.notify(event)

    for t in threading.enumerate():
        if t.daemon and t.is_alive():
            t.join(timeout=2)

    failing.send.assert_called_once_with(event)
    working.send.assert_called_once_with(event)


def test_empty_dispatcher_is_noop():
    dispatcher = NotificationDispatcher([])
    assert dispatcher.enabled is False

    # Should not raise
    dispatcher.notify(NotificationEvent(
        event_type=EventType.ACTION_NEEDED,
        message="ignored",
    ))


def test_dispatcher_enabled_with_notifiers():
    dispatcher = NotificationDispatcher([MagicMock()])
    assert dispatcher.enabled is True


# ── TerminalNotifier ─────────────────────────────────────────────────


def test_terminal_writes_bel_to_stderr():
    notifier = TerminalNotifier()
    event = NotificationEvent(
        event_type=EventType.INSERT_DISC,
        message="insert disc 2",
    )

    with patch("ripper.notifications.terminal.sys.stderr") as mock_stderr:
        notifier.send(event)
        mock_stderr.write.assert_called_once_with("\a")
        mock_stderr.flush.assert_called_once()


# ── SlackNotifier ────────────────────────────────────────────────────


def test_slack_rejects_empty_webhook_url():
    with pytest.raises(ValueError, match="must not be empty"):
        SlackNotifier("")


def test_slack_sends_correct_payload():
    notifier = SlackNotifier("https://hooks.slack.com/test")
    event = NotificationEvent(
        event_type=EventType.RIP_COMPLETE,
        message="Rip complete: Movie",
        disc_name="MOVIE_DISC",
    )

    with patch("ripper.notifications.slack.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__ = MagicMock()
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        notifier.send(event)

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["text"] == (
            "*[rip]* Rip Complete: Rip complete: Movie (MOVIE_DISC)"
        )
        assert req.get_header("Content-type") == "application/json"
        assert req.method == "POST"


def test_slack_sends_payload_without_disc_name():
    notifier = SlackNotifier("https://hooks.slack.com/test")
    event = NotificationEvent(
        event_type=EventType.ACTION_NEEDED,
        message="Choose mode",
    )

    with patch("ripper.notifications.slack.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__ = MagicMock()
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        notifier.send(event)

        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["text"] == (
            "*[rip]* Action Needed: Choose mode"
        )


def test_slack_logs_warning_on_network_error(caplog):
    notifier = SlackNotifier("https://hooks.slack.com/test")
    event = NotificationEvent(
        event_type=EventType.RIP_FAILED,
        message="fail",
    )

    with patch(
        "ripper.notifications.slack.urllib.request.urlopen",
        side_effect=OSError("network down"),
    ):
        # Should not raise
        notifier.send(event)

    assert any(
        "Slack notification failed" in r.message
        for r in caplog.records
    )


# ── create_dispatcher ────────────────────────────────────────────────


def test_create_dispatcher_default_settings():
    from ripper.config.settings import Settings

    settings = Settings(
        notify_terminal=True,
        notify_slack_webhook_url="",
    )
    dispatcher = create_dispatcher(settings)
    assert dispatcher.enabled is True
    assert len(dispatcher._notifiers) == 1
    assert isinstance(dispatcher._notifiers[0], TerminalNotifier)


def test_create_dispatcher_with_slack():
    from ripper.config.settings import Settings

    settings = Settings(
        notify_terminal=True,
        notify_slack_webhook_url="https://hooks.slack.com/test",
    )
    dispatcher = create_dispatcher(settings)
    assert dispatcher.enabled is True
    assert len(dispatcher._notifiers) == 2
    assert isinstance(dispatcher._notifiers[0], TerminalNotifier)
    assert isinstance(dispatcher._notifiers[1], SlackNotifier)


def test_create_dispatcher_all_disabled():
    from ripper.config.settings import Settings

    settings = Settings(
        notify_terminal=False,
        notify_slack_webhook_url="",
    )
    dispatcher = create_dispatcher(settings)
    assert dispatcher.enabled is False
    assert len(dispatcher._notifiers) == 0
