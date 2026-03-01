"""Display helpers for progress, extras classification, and title tables."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ripper.notifications import NotificationDispatcher

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ripper.core.disc import DiscInfo, ExtraType, Title
from ripper.core.ripper import RipProgress
from ripper.metadata.classifier import classify_extra
from ripper.utils.formatting import fmt_duration, fmt_rate, fmt_size
from ripper.utils.matching import find_title_for_mkv

console = Console()

_BAR_WIDTH = 30


def format_progress_line(progress: RipProgress) -> str:
    """Build the terminal progress line for a single update."""
    pct = progress.percent
    filled = int(_BAR_WIDTH * pct / 100)
    bar = "\u2588" * filled + "\u2591" * (_BAR_WIDTH - filled)
    title = (progress.title_name or "Working")[:34]

    parts = [f"\r  {title:<34s} {bar}  {pct:5.1f}%"]
    if progress.total_bytes > 0:
        parts.append(
            f"  {fmt_size(progress.current_bytes)}"
            f" / {fmt_size(progress.total_bytes)}"
        )
    elif progress.current_bytes > 0:
        parts.append(f"  {fmt_size(progress.current_bytes)}")
    else:
        if progress.title_name and progress.title_name != "Starting MakeMKV":
            parts.append("  Working...")
        else:
            parts.append("  Initializing...")
    if progress.bytes_per_second and progress.bytes_per_second > 0:
        parts.append(f"  {fmt_rate(progress.bytes_per_second)}")
    if progress.eta_seconds is not None:
        parts.append(f"  ETA: {fmt_duration(progress.eta_seconds)}")
    return "".join(parts)


def print_progress(progress: RipProgress) -> None:
    """Print single-line progress update with carriage return."""
    line = format_progress_line(progress)
    sys.stdout.write(f"{line:<100s}")
    sys.stdout.flush()


def start_rip_with_status(
    label: str,
    rip_fn,
    *args,
    **kwargs,
) -> None:
    """Print a label and immediate progress line, then rip."""
    console.print()
    console.print(f"  [bold]{label}[/]")
    console.print("  [dim]Starting MakeMKV...[/]")
    on_progress = kwargs.get("on_progress")
    if on_progress:
        on_progress(
            RipProgress(
                title_id=0,
                title_name="Starting MakeMKV",
                percent=0.0,
                current_bytes=0,
                total_bytes=0,
                eta_seconds=None,
            )
        )
    rip_fn(*args, **kwargs)
    # Newline after the \r progress line
    console.print()


class ConcurrentProgress:
    """Context manager for displaying multiple progress bars at once.

    Used in batch mode to show backup and remux progress simultaneously.
    Single-disc mode continues using print_progress() directly.
    """

    def __init__(self) -> None:
        self._slots: dict[str, RipProgress] = {}
        self._lock = threading.Lock()
        self._live: Live | None = None

    def __enter__(self) -> "ConcurrentProgress":
        self._live = Live(
            self._render(), console=console, refresh_per_second=4,
        )
        self._live.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        if self._live:
            self._live.__exit__(*exc)
            self._live = None

    def update(self, slot_id: str, progress: RipProgress) -> None:
        """Update progress for a named slot."""
        with self._lock:
            self._slots[slot_id] = progress
        if self._live:
            self._live.update(self._render())

    def remove(self, slot_id: str) -> None:
        """Remove a slot when its operation completes."""
        with self._lock:
            self._slots.pop(slot_id, None)
        if self._live:
            self._live.update(self._render())

    def make_callback(self, slot_id: str):
        """Return a ProgressCallback bound to a specific slot."""
        def _cb(progress: RipProgress) -> None:
            self.update(slot_id, progress)
        return _cb

    def _render(self) -> Panel:
        """Build a Rich Panel with one progress bar per active slot."""
        with self._lock:
            slots = dict(self._slots)

        if not slots:
            return Panel(Text("  Waiting...", style="dim"), border_style="cyan")

        lines = Text()
        for slot_id, progress in slots.items():
            line = format_progress_line(progress)
            # Strip leading \r from format_progress_line
            clean = line.lstrip("\r")
            label = slot_id.capitalize()
            lines.append(f"  {label}: ", style="bold")
            lines.append(clean.strip())
            lines.append("\n")

        return Panel(lines, title="Progress", border_style="cyan")


def classify_extras_interactive(
    extras: list[Path],
    disc_info: DiscInfo | None = None,
    dispatcher: "NotificationDispatcher | None" = None,
) -> dict[Path, ExtraType]:
    """Interactive extras classification using a selection menu."""
    from ripper.notifications import EventType, NotificationEvent

    classifications: dict[Path, ExtraType] = {}

    if dispatcher and dispatcher.enabled:
        dispatcher.notify(NotificationEvent(
            event_type=EventType.ACTION_NEEDED,
            message="Classify extras",
        ))

    console.print()
    console.print("  [bold]Classify extras for Emby:[/]")
    console.print()

    # Build title lookup from disc_info for DiscDB names
    discdb_titles = {}
    if disc_info:
        for t in disc_info.titles:
            if t.discdb_info and t.discdb_info.item_title:
                discdb_titles[t.id] = t

    discdb_title_list = list(discdb_titles.values())

    for i, path in enumerate(extras):
        size = path.stat().st_size if path.exists() else 0

        # Try to match this file to a disc title with DiscDB info
        suggested = None
        label = path.stem
        matched = find_title_for_mkv(path, discdb_title_list)
        if matched:
            label = matched.discdb_info.item_title  # type: ignore[union-attr]
            suggested = matched.suggested_extra_type

        if suggested is None:
            suggested = classify_extra(path.stem)

        classifications[path] = suggested
        console.print(
            f"  [cyan]{i + 1:>2d}[/]  {label[:40]:<40s}  "
            f"{fmt_size(size):>8s}  [dim][{suggested.value}][/]"
        )

    console.print()
    console.print(
        "  [dim]Change: '<number> <category>'"
        " (e.g. '1 featurettes')[/]"
    )
    console.print(
        "  [dim]Categories: extras, behind the scenes,"
        " deleted scenes,[/]"
    )
    console.print(
        "  [dim]  featurettes, interviews, scenes,"
        " shorts, trailers[/]"
    )
    console.print("  [dim]Press Enter to accept all.[/]")

    valid_types = {et.value: et for et in ExtraType}
    extras_list = list(extras)

    while True:
        console.print()
        try:
            raw = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not raw:
            break

        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            console.print("  [red]Format: <number> <category>[/]")
            continue

        try:
            idx = int(parts[0])
        except ValueError:
            console.print("  [red]Invalid number[/]")
            continue

        if idx < 1 or idx > len(extras_list):
            console.print(
                f"  [red]Number must be 1-{len(extras_list)}[/]"
            )
            continue

        category = parts[1].lower()
        if category not in valid_types:
            console.print(f"  [red]Unknown category: {category}[/]")
            continue

        path = extras_list[idx - 1]
        classifications[path] = valid_types[category]
        console.print(f"  [green]{idx} -> {category}[/]")

    return classifications


def title_display_name(t: Title) -> str:
    """Best display name for a title: DiscDB item_title or raw name."""
    if t.discdb_info and t.discdb_info.item_title:
        return t.discdb_info.item_title
    return t.name


def _title_type_label(t: Title) -> str:
    """Build the Type column label for a title."""
    if t.discdb_info:
        item_type = t.discdb_info.item_type
        if item_type == "Episode" and t.discdb_info.season is not None:
            return f"[green]S{t.discdb_info.season}E{t.discdb_info.episode}[/]"
        return f"[green]{item_type}[/]"
    if t.suggested_extra_type:
        return f"[dim]{t.suggested_extra_type.value}[/]"
    if t.is_main_feature:
        return "[bold]Main[/]"
    return ""


def print_title_table(
    disc_info: DiscInfo, show_source: bool = False,
) -> None:
    """Print title table using Rich."""
    table = Table(show_header=True, padding=(0, 1))
    table.add_column("", width=1)
    table.add_column("ID", justify="right", width=3)
    if show_source:
        table.add_column("Source", min_width=12)
    table.add_column("Name", min_width=30)
    table.add_column("Type", min_width=10)
    table.add_column("Duration", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Ch", justify="right")

    for t in disc_info.titles:
        marker = "[bold]*[/]" if t.is_main_feature else ""
        row = [
            marker,
            str(t.id),
        ]
        if show_source:
            row.append(f"[dim]{t.source_file}[/]" if t.source_file else "")
        row.extend([
            title_display_name(t)[:45],
            _title_type_label(t),
            t.duration_display,
            t.size_display,
            str(t.chapter_count),
        ])
        table.add_row(*row)

    console.print(table)
