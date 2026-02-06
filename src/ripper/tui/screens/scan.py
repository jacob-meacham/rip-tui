"""Title selection screen for choosing specific titles to rip."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Label

from ripper.config.settings import Settings
from ripper.core.disc import DiscInfo


class ScanScreen(Screen):
    """Display scanned titles with selection checkboxes."""

    BINDINGS = [
        Binding("a", "select_all", "Select All"),
        Binding("n", "select_none", "Select None"),
        Binding("escape", "go_back", "Back"),
    ]

    def __init__(
        self, settings: Settings, disc_info: DiscInfo
    ) -> None:
        super().__init__()
        self.settings = settings
        self.disc_info = disc_info
        self.selected_ids: set[int] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="scan-container"):
            yield Label("[bold]Select Titles to Rip[/]")
            yield DataTable(id="title-table")
            with Horizontal(classes="button-row"):
                yield Button(
                    "Rip Selected",
                    variant="primary",
                    id="btn-rip",
                )
                yield Button("Back", id="btn-back")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns(
            "Sel", "ID", "Name", "Duration", "Size", "Chapters"
        )

        for title in self.disc_info.titles:
            marker = "[X]" if title.is_main_feature else "[ ]"
            if title.is_main_feature:
                self.selected_ids.add(title.id)
            table.add_row(
                marker,
                str(title.id),
                title.name[:40],
                title.duration_display,
                title.size_display,
                str(title.chapter_count),
                key=str(title.id),
            )

    def on_data_table_row_selected(
        self, event: DataTable.RowSelected
    ) -> None:
        """Toggle selection on row click."""
        table = self.query_one(DataTable)
        title_id = int(str(event.row_key.value))

        if title_id in self.selected_ids:
            self.selected_ids.discard(title_id)
            table.update_cell_at(
                (event.cursor_row, 0), "[ ]"
            )
        else:
            self.selected_ids.add(title_id)
            table.update_cell_at(
                (event.cursor_row, 0), "[X]"
            )

    def action_select_all(self) -> None:
        table = self.query_one(DataTable)
        for i, title in enumerate(self.disc_info.titles):
            self.selected_ids.add(title.id)
            table.update_cell_at((i, 0), "[X]")

    def action_select_none(self) -> None:
        table = self.query_one(DataTable)
        self.selected_ids.clear()
        for i in range(len(self.disc_info.titles)):
            table.update_cell_at((i, 0), "[ ]")

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-back":
            self.app.pop_screen()
            return

        if not self.selected_ids:
            self.notify(
                "Select at least one title",
                severity="warning",
            )
            return

        from ripper.tui.screens.main import ConfirmRipScreen

        self.app.pop_screen()
        self.app.push_screen(
            ConfirmRipScreen(
                self.settings,
                self.disc_info,
                self.disc_info.name,
                "select",
                selected_title_ids=self.selected_ids,
            )
        )
