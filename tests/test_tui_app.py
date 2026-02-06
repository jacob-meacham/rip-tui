"""Tests for TUI app helpers."""

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
