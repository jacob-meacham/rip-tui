"""Inline interactive CLI for ripping discs."""

import asyncio
import inspect
import logging
import threading
from collections.abc import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from simple_term_menu import TerminalMenu

from ripper.config.settings import Settings
from ripper.core.disc import DiscInfo, MediaType
from ripper.core.ripper import RipCancelledError
from ripper.core.scanner import scan_disc
from ripper.metadata.classifier import classify_titles, detect_media_type
from ripper.metadata.matcher import clean_disc_name
from ripper.tui.display import print_title_table
from ripper.tui.flows import (
    rip_movie_full,
    rip_movie_main,
    rip_multi_disc,
    rip_selected,
    rip_tv,
)
from ripper.utils.formatting import fmt_duration, fmt_size

logger = logging.getLogger(__name__)

console = Console()

# Inputs that should return to the main menu from prompts.
_BACK_COMMANDS = {"b", "back", "q", "quit"}


def _is_back_command(raw: str) -> bool:
    """Return True if input should trigger a back-to-menu action."""
    return raw.strip().lower() in _BACK_COMMANDS


def run_interactive(settings: Settings) -> None:
    """Main interactive CLI flow."""
    # Interactive mode should stay visually clean.
    logging.getLogger().setLevel(logging.WARNING)

    console.print()

    # Scan disc (with spinner)
    disc_info = _scan_disc(settings)
    if disc_info is None:
        return

    # Kick off TMDb lookup in background — will update disc_info
    # before the user finishes navigating prompts
    tmdb_thread = _start_tmdb_lookup(disc_info, settings)

    # Menu loop — flows return here on cancel/back
    while True:
        choice = _show_menu()
        if choice is None:
            return

        # Wait for TMDb before any flow that needs a name
        if choice != 5:
            _await_tmdb(tmdb_thread)

        if choice == 0:
            _flow_movie(settings, disc_info, mode="full")
        elif choice == 1:
            _flow_movie(settings, disc_info, mode="main")
        elif choice == 2:
            _flow_movie(settings, disc_info, mode="multi")
        elif choice == 3:
            _flow_tv(settings, disc_info)
        elif choice == 4:
            _await_tmdb(tmdb_thread)
            _flow_select(settings, disc_info)
        elif choice == 5:
            _show_disc_info(disc_info)


# ── Scanning ─────────────────────────────────────────────────────────


def _scan_disc(settings: Settings) -> DiscInfo | None:
    """Scan disc with a live spinner, then print summary."""
    with console.status("  Scanning disc...", spinner="dots"):
        try:
            disc_info = scan_disc(settings)
        except Exception as e:
            console.print(f"  [red]Scan failed: {e}[/]")
            return None

        classify_titles(disc_info.titles, settings.min_main_length)
        disc_info.detected_media_type = detect_media_type(
            disc_info.titles, settings.min_main_length
        )

    cleaned = clean_disc_name(disc_info.name)
    media_label = {
        MediaType.MOVIE: "Movie",
        MediaType.TV_SHOW: "TV Show",
        MediaType.UNKNOWN: "Unknown",
    }[disc_info.detected_media_type]

    main_count = len(disc_info.main_titles)
    extra_count = len(disc_info.extra_titles)
    total = len(disc_info.titles)

    summary = Table.grid(padding=(0, 1))
    summary.add_column(style="cyan", justify="right")
    summary.add_column()
    summary.add_row("Disc", f"[dim]{disc_info.name}[/]")
    summary.add_row("Detected", f"[bold]{cleaned}[/] ({media_label})")
    summary.add_row(
        "Titles",
        f"{total} total ({main_count} main, {extra_count} extras)",
    )
    console.print(Panel.fit(summary, border_style="cyan"))
    console.print()

    return disc_info


def _start_tmdb_lookup(
    disc_info: DiscInfo, settings: Settings
) -> threading.Thread | None:
    """Start TMDb lookup in a background thread."""
    if not settings.auto_lookup or not settings.tmdb_api_key:
        return None

    cleaned = clean_disc_name(disc_info.name)

    def _run() -> None:
        from ripper.metadata.matcher import match_title
        from ripper.metadata.tmdb import TMDbClient

        async def _lookup() -> dict | None:
            client = TMDbClient(settings.tmdb_api_key)
            try:
                results = await client.search_movie(cleaned)
                return match_title(
                    cleaned,
                    results,
                    threshold=settings.fuzzy_threshold,
                )
            finally:
                await client.close()

        try:
            match = asyncio.run(_lookup())
        except Exception:
            logger.debug("TMDb lookup failed", exc_info=True)
            return

        if match:
            disc_info.tmdb_id = match.get("id")
            title = match.get("title", "")
            year = match.get("release_date", "")[:4]
            disc_info.tmdb_title = title
            if year:
                disc_info.year = int(year)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _await_tmdb(thread: threading.Thread | None) -> bool:
    """Wait for background TMDb lookup to finish.

    Returns True if the thread completed, False if it timed out
    (meaning disc_info may not have TMDb data populated).
    """
    if thread is None:
        return True
    if not thread.is_alive():
        return True
    console.print("  [dim]Fetching metadata...[/]")
    thread.join(timeout=10)
    if thread.is_alive():
        console.print(
            "  [dim]TMDb lookup timed out, continuing without it[/]"
        )
        return False
    return True


# ── Menu ─────────────────────────────────────────────────────────────

_MENU_ITEMS = [
    "Movie with extras",
    "Main feature only",
    "Multi-disc movie",
    "TV episodes",
    "Select specific titles",
    "View disc info",
]


def _build_terminal_menu(
    entries: Sequence[str], **kwargs
) -> TerminalMenu:
    """Build a TerminalMenu while tolerating older library versions."""
    supported = inspect.signature(TerminalMenu.__init__).parameters
    compatible_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key in supported
    }
    return TerminalMenu(entries, **compatible_kwargs)


def _show_menu() -> int | None:
    """Show scrollable action menu. Returns index or None to quit."""
    console.print("  [bold]What do you want to rip?[/]")
    console.print()

    menu = _build_terminal_menu(
        _MENU_ITEMS,
        title="  Enter to select. Esc to quit.",
        show_menu_entry_index=False,
        cycle_cursor=True,
        menu_cursor_style=("fg_cyan", "bold"),
        menu_highlight_style=("fg_cyan", "bold"),
    )
    idx = menu.show()
    console.print()

    if idx is None:
        return None
    return idx


# ── Prompts ──────────────────────────────────────────────────────────


def _suggested_name(disc_info: DiscInfo) -> str:
    """Best movie name from TMDb or disc name."""
    if disc_info.tmdb_title and disc_info.year:
        return f"{disc_info.tmdb_title} ({disc_info.year})"
    if disc_info.tmdb_title:
        return disc_info.tmdb_title
    return clean_disc_name(disc_info.name)


def _prompt_movie_name(disc_info: DiscInfo) -> str | None:
    """Prompt for movie name. Returns None to go back."""
    suggested = _suggested_name(disc_info)
    console.print()
    try:
        name = input(
            f"  Movie name [{suggested}] (b=back): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return None
    if _is_back_command(name):
        return None
    return name or suggested


def _prompt_disc_count() -> int | None:
    """Prompt for number of discs."""
    try:
        raw = input("  Number of discs [2] (b=back): ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return None
    if _is_back_command(raw):
        return None
    if not raw:
        return 2
    try:
        return int(raw)
    except ValueError:
        console.print("  [red]Invalid number[/]")
        return None


def _prompt_tv_info() -> tuple[str, int] | None:
    """Prompt for TV show name and season."""
    console.print()
    try:
        show = input("  Show name (b=back): ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return None
    if _is_back_command(show):
        return None
    if not show:
        console.print("  [red]Show name cannot be empty[/]")
        return None

    try:
        raw = input("  Season number [1] (b=back): ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return None
    if _is_back_command(raw):
        return None
    season = 1
    if raw:
        try:
            season = int(raw)
        except ValueError:
            console.print("  [red]Invalid season number[/]")
            return None

    return show, season


def _select_titles(disc_info: DiscInfo) -> set[int] | None:
    """Multi-select title list. Returns selected IDs or None."""
    console.print()

    # Build menu entries with title details
    entries: list[str] = []
    for t in disc_info.titles:
        marker = "*" if t.is_main_feature else " "
        entries.append(
            f"{marker} {t.id:>2d}  {t.name[:35]:<35s}"
            f"  {t.duration_display:>11s}"
            f"  {t.size_display:>8s}"
        )

    # Pre-select main features
    preselected = [
        i
        for i, t in enumerate(disc_info.titles)
        if t.is_main_feature
    ]

    menu = _build_terminal_menu(
        entries,
        title=(
            "  Space to toggle, Enter to confirm, Esc to go back."
        ),
        show_menu_entry_index=False,
        cycle_cursor=True,
        multi_select=True,
        show_multi_select_hint=True,
        multi_select_select_on_accept=False,
        multi_select_empty_ok=False,
        preselected_entries=preselected,
        menu_cursor_style=("fg_cyan", "bold"),
        menu_highlight_style=("fg_cyan", "bold"),
    )
    result = menu.show()
    console.print()

    if result is None:
        return None

    # result is a tuple of selected indices
    selected = set()
    titles = disc_info.titles
    if isinstance(result, int):
        selected.add(titles[result].id)
    else:
        for idx in result:
            selected.add(titles[idx].id)

    if not selected:
        return None
    return selected


# ── Confirmation ─────────────────────────────────────────────────────


def _confirm_rip(
    disc_info: DiscInfo,
    name: str,
    mode: str,
    disc_count: int = 1,
    season_num: int = 1,
    selected_ids: set[int] | None = None,
) -> bool:
    """Print rip summary and ask for confirmation."""
    mode_labels = {
        "full": "Movie with all extras",
        "main": "Main feature only",
        "multi": f"Multi-disc movie ({disc_count} discs)",
        "tv": f"TV Season {season_num}",
        "select": "Selected titles",
    }

    titles = _get_titles(disc_info, mode, selected_ids)
    total_size = sum(t.size_bytes for t in titles)
    total_dur = sum(t.duration_seconds for t in titles)

    console.print()
    console.print(f"  [bold]Ready to rip: {name}[/]")
    console.print(f"  Mode: {mode_labels.get(mode, mode)}")
    console.print(
        f"  Titles: {len(titles)}"
        f" | ~{fmt_size(total_size)}"
        f" | {fmt_duration(total_dur)}"
    )
    console.print()

    for t in titles:
        marker = "*" if t.is_main_feature else " "
        console.print(
            f"   {marker} {t.id:>2d}  {t.name[:35]:<35s}  "
            f"{t.duration_display:>11s}  {t.size_display:>8s}"
        )

    console.print()
    try:
        answer = input("  Start rip? [Y/n/b]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False
    if _is_back_command(answer):
        return False
    return answer in ("", "y", "yes")


def _get_titles(
    disc_info: DiscInfo,
    mode: str,
    selected_ids: set[int] | None = None,
) -> list:
    """Get the list of titles for a given mode."""
    if mode == "main":
        return disc_info.main_titles
    if selected_ids is not None:
        return [t for t in disc_info.titles if t.id in selected_ids]
    return disc_info.titles


# ── Disc Info ────────────────────────────────────────────────────────


def _show_disc_info(disc_info: DiscInfo) -> None:
    """Print a formatted title table."""
    console.print()
    print_title_table(disc_info)
    console.print()


# ── Rip Flows ────────────────────────────────────────────────────────
# Each flow returns normally when done or cancelled.
# The main loop always continues after a flow returns.


def _flow_movie(
    settings: Settings, disc_info: DiscInfo, mode: str
) -> None:
    """Movie rip flow (full, main, or multi)."""
    name = _prompt_movie_name(disc_info)
    if not name:
        return

    disc_count = 1
    if mode == "multi":
        disc_count = _prompt_disc_count()
        if disc_count is None:
            return

    if not _confirm_rip(
        disc_info, name, mode, disc_count=disc_count
    ):
        return

    try:
        if mode == "full":
            rip_movie_full(settings, disc_info, name)
        elif mode == "main":
            rip_movie_main(settings, disc_info, name)
        elif mode == "multi":
            rip_multi_disc(settings, disc_info, name, disc_count)
    except RipCancelledError:
        console.print("\n  [yellow]Cancelled by user.[/]")
    except Exception as e:
        console.print(f"\n  [red]Error: {e}[/]")
        logger.error("Rip failed: %s", e, exc_info=True)


def _flow_tv(settings: Settings, disc_info: DiscInfo) -> None:
    """TV episode rip flow."""
    result = _prompt_tv_info()
    if result is None:
        return
    show, season = result

    if not _confirm_rip(disc_info, show, "tv", season_num=season):
        return

    try:
        rip_tv(settings, disc_info, show, season)
    except RipCancelledError:
        console.print("\n  [yellow]Cancelled by user.[/]")
    except Exception as e:
        console.print(f"\n  [red]Error: {e}[/]")
        logger.error("Rip failed: %s", e, exc_info=True)


def _flow_select(
    settings: Settings, disc_info: DiscInfo
) -> None:
    """Selected titles rip flow."""
    selected_ids = _select_titles(disc_info)
    if not selected_ids:
        return

    name = _suggested_name(disc_info)

    if not _confirm_rip(
        disc_info, name, "select", selected_ids=selected_ids
    ):
        return

    try:
        rip_selected(settings, disc_info, name, selected_ids)
    except RipCancelledError:
        console.print("\n  [yellow]Cancelled by user.[/]")
    except Exception as e:
        console.print(f"\n  [red]Error: {e}[/]")
        logger.error("Rip failed: %s", e, exc_info=True)
