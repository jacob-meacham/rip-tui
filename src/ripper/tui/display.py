"""Display helpers for progress, extras classification, and title tables."""

import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ripper.core.disc import DiscInfo, ExtraType
from ripper.core.ripper import RipProgress
from ripper.metadata.classifier import classify_extra
from ripper.utils.formatting import fmt_duration, fmt_rate, fmt_size

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


def classify_extras_interactive(
    extras: list[Path],
) -> dict[Path, ExtraType]:
    """Interactive extras classification using a selection menu."""
    classifications: dict[Path, ExtraType] = {}

    console.print()
    console.print("  [bold]Classify extras for Emby:[/]")
    console.print()

    for i, path in enumerate(extras):
        size = path.stat().st_size if path.exists() else 0
        suggested = classify_extra(path.stem)
        classifications[path] = suggested
        console.print(
            f"  [cyan]{i + 1:>2d}[/]  {path.name[:40]:<40s}  "
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


def print_title_table(disc_info: DiscInfo) -> None:
    """Print title table using Rich."""
    table = Table(show_header=True, padding=(0, 1))
    table.add_column("", width=1)
    table.add_column("ID", justify="right", width=3)
    table.add_column("Name", min_width=30)
    table.add_column("Duration", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Ch", justify="right")

    for t in disc_info.titles:
        marker = "[bold]*[/]" if t.is_main_feature else ""
        table.add_row(
            marker,
            str(t.id),
            t.name[:45],
            t.duration_display,
            t.size_display,
            str(t.chapter_count),
        )

    console.print(table)
