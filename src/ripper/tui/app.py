"""Main Textual application."""

from textual.app import App
from textual.binding import Binding

from ripper.config.settings import Settings
from ripper.tui.screens.main import MainScreen


class RipperApp(App):
    """4K Blu-ray Ripper TUI."""

    TITLE = "Ripper"
    CSS_PATH = "styles/app.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.settings = settings or Settings()

    def on_mount(self) -> None:
        """Push the main screen and auto-scan."""
        self.push_screen(MainScreen(self.settings))
