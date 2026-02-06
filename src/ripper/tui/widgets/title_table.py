"""Reusable title table widget."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable

from ripper.core.disc import Title


class TitleTable(Widget):
    """DataTable displaying disc titles with selection support."""

    def __init__(self, titles: list[Title], selectable: bool = True) -> None:
        super().__init__()
        self.titles = titles
        self.selectable = selectable
        self.selected_ids: set[int] = set()

    def compose(self) -> ComposeResult:
        yield DataTable(id="titles")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"

        if self.selectable:
            table.add_columns("Sel", "ID", "Name", "Duration", "Size", "Chapters")
        else:
            table.add_columns("ID", "Name", "Duration", "Size", "Chapters")

        for title in self.titles:
            if self.selectable:
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
            else:
                table.add_row(
                    str(title.id),
                    title.name[:40],
                    title.duration_display,
                    title.size_display,
                    str(title.chapter_count),
                    key=str(title.id),
                )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if not self.selectable:
            return

        table = self.query_one(DataTable)
        title_id = int(str(event.row_key.value))

        if title_id in self.selected_ids:
            self.selected_ids.discard(title_id)
            table.update_cell_at((event.cursor_row, 0), "[ ]")
        else:
            self.selected_ids.add(title_id)
            table.update_cell_at((event.cursor_row, 0), "[X]")
