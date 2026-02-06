"""Tests for TUI app helpers."""

from ripper.core.ripper import RipProgress
from ripper.tui import app


def test_build_terminal_menu_ignores_unsupported_kwargs(
    monkeypatch,
):
    class FakeTerminalMenu:
        def __init__(self, entries, title=None):
            self.entries = entries
            self.title = title

    monkeypatch.setattr(app, "TerminalMenu", FakeTerminalMenu)

    menu = app._build_terminal_menu(
        ["one", "two"],
        title="My menu",
        cycle_cursor=True,
        show_menu_entry_index=False,
    )

    assert isinstance(menu, FakeTerminalMenu)
    assert menu.entries == ["one", "two"]
    assert menu.title == "My menu"


def test_format_progress_line_shows_title_and_init_status():
    progress = RipProgress(
        title_id=0,
        title_name="Starting MakeMKV",
        percent=0.0,
        current_bytes=0,
        total_bytes=0,
        eta_seconds=None,
    )

    line = app._format_progress_line(progress)

    assert "Starting MakeMKV" in line
    assert "Initializing..." in line


def test_format_progress_line_shows_size_progress():
    progress = RipProgress(
        title_id=1,
        title_name="Main Feature",
        percent=42.5,
        current_bytes=1_048_576,
        total_bytes=10_737_418_240,
        eta_seconds=120,
        bytes_per_second=32_768_000,
    )

    line = app._format_progress_line(progress)

    assert "Main Feature" in line
    assert "42.5%" in line
    assert "1 MB / 10.0 GB" in line
    assert "31.2 MB/s" in line
    assert "ETA: 0h 02m 00s" in line
