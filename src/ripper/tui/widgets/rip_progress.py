"""Rip progress display widget."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import ProgressBar, Static

from ripper.core.ripper import RipProgress
from ripper.utils.formatting import fmt_size


class RipProgressWidget(Widget):
    """Displays ripping progress for a single title."""

    def __init__(self) -> None:
        super().__init__()
        self._completed: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Waiting...", id="progress-title")
            yield ProgressBar(id="progress-bar", total=100)
            yield Static("", id="progress-detail")
            yield Static("", id="progress-completed")

    def update_progress(self, progress: RipProgress) -> None:
        """Update the widget with new progress data."""
        self.query_one("#progress-title", Static).update(
            f"Title {progress.title_id}: {progress.title_name}"
        )
        self.query_one(ProgressBar).update(progress=progress.percent)
        current = fmt_size(progress.current_bytes)
        total = fmt_size(progress.total_bytes)
        self.query_one("#progress-detail", Static).update(
            f"{progress.percent:.1f}%  |  {current} / {total}"
        )

    def mark_completed(self, title_name: str, size_display: str) -> None:
        """Mark a title as completed in the list."""
        self._completed.append(f"  [green]OK[/] {title_name} ({size_display})")
        self.query_one("#progress-completed", Static).update(
            "[bold]Completed:[/]\n" + "\n".join(self._completed)
        )
