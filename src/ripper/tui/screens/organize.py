"""Extras classification screen for interactive organization."""

from collections.abc import Callable
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Label

from ripper.core.disc import ExtraType
from ripper.metadata.classifier import classify_extra
from ripper.utils.formatting import fmt_size


class OrganizeScreen(Screen):
    """Interactive extras classification into Emby categories."""

    BINDINGS = [Binding("escape", "go_back", "Back")]

    def __init__(
        self,
        extras: list[Path],
        on_complete: Callable[[dict[Path, ExtraType]], None],
    ) -> None:
        super().__init__()
        self.extras = extras
        self.on_complete = on_complete
        self.classifications: dict[Path, ExtraType] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="organize-container"):
            yield Label("[bold]Classify Extras for Emby[/]")
            yield DataTable(id="extras-table")
            with Horizontal(classes="button-row"):
                yield Button("Apply", variant="primary", id="btn-apply")
                yield Button("All as Extras", id="btn-all-extras")
                yield Button("Cancel", id="btn-cancel")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("File", "Size", "Category")

        for extra_path in self.extras:
            size = (
                extra_path.stat().st_size
                if extra_path.exists()
                else 0
            )
            # Auto-classify based on filename patterns
            suggested = classify_extra(extra_path.stem)
            extra_type = suggested or ExtraType.EXTRAS
            self.classifications[extra_path] = extra_type
            table.add_row(
                extra_path.name[:50],
                fmt_size(size),
                extra_type.value,
                key=str(extra_path),
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Cycle through extra types on row click."""
        table = self.query_one(DataTable)
        path_str = str(event.row_key.value)
        path = Path(path_str)

        if path not in self.classifications:
            return

        # Cycle to next extra type
        types = list(ExtraType)
        current = self.classifications[path]
        next_idx = (types.index(current) + 1) % len(types)
        new_type = types[next_idx]

        self.classifications[path] = new_type
        table.update_cell_at((event.cursor_row, 2), new_type.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn-apply":
                self.on_complete(self.classifications)
                self.app.pop_screen()
            case "btn-all-extras":
                self.classifications = {p: ExtraType.EXTRAS for p in self.extras}
                self.on_complete(self.classifications)
                self.app.pop_screen()
            case "btn-cancel":
                self.on_complete(self.classifications)
                self.app.pop_screen()

    def action_go_back(self) -> None:
        self.on_complete(self.classifications)
        self.app.pop_screen()
