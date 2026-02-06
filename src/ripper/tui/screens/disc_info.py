"""Disc info display screen."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

from ripper.core.disc import DiscInfo


class DiscInfoScreen(Screen):
    """Full-screen display of disc title information."""

    BINDINGS = [Binding("escape", "go_back", "Back")]

    def __init__(self, disc_info: DiscInfo) -> None:
        super().__init__()
        self.disc_info = disc_info

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="scan-container"):
            yield Label(
                f"[bold]Disc: {self.disc_info.name}[/]"
            )
            yield DataTable(id="info-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns(
            " ", "ID", "Name", "Duration", "Size", "Chapters"
        )

        for title in self.disc_info.titles:
            marker = "*" if title.is_main_feature else ""
            table.add_row(
                marker,
                str(title.id),
                title.name[:45],
                title.duration_display,
                title.size_display,
                str(title.chapter_count),
            )

    def action_go_back(self) -> None:
        self.app.pop_screen()
