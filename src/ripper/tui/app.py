"""Inline interactive CLI for ripping discs."""

from __future__ import annotations

import asyncio
import inspect
import logging
import shutil
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ripper.notifications import NotificationDispatcher

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from simple_term_menu import TerminalMenu

from ripper.config.settings import Settings
from ripper.core.disc import DiscInfo, MediaType
from ripper.core.organizer import organize_movie, organize_tv
from ripper.core.ripper import RipCancelledError
from ripper.core.scanner import scan_disc
from ripper.metadata.classifier import (
    classify_titles,
    detect_media_type,
)
from ripper.metadata.matcher import clean_disc_name
from ripper.tui.display import (
    ConcurrentProgress,
    print_title_table,
    title_display_name,
)
from ripper.tui.flows import (
    RemuxHandle,
    backup_is_valid,
    classify_and_organize_movie,
    cleanup_backup,
    create_backup,
    enrich_disc_info,
    rip_movie_full,
    rip_movie_main,
    rip_multi_disc,
    rip_selected,
    rip_tv,
    select_remux_titles,
    start_remux_background,
)
from ripper.utils.drive import eject_disc, wait_for_disc
from ripper.utils.formatting import fmt_duration, fmt_size, sanitize_filename

logger = logging.getLogger(__name__)

console = Console()

# Inputs that should return to the main menu from prompts.
_BACK_COMMANDS = {"b", "back", "q", "quit"}


def _is_back_command(raw: str) -> bool:
    """Return True if input should trigger a back-to-menu action."""
    return raw.strip().lower() in _BACK_COMMANDS


def run_interactive(
    settings: Settings,
    external_backup: Path | None = None,
    verbose: bool = False,
) -> None:
    """Main interactive CLI flow.

    Args:
        settings: App settings.
        external_backup: Optional path to a pre-existing backup dir.
            When provided, skips the backup step and never cleans up
            the backup on exit.
        verbose: Show debug output including source files and DiscDB matching.
    """
    # Interactive mode should stay visually clean, unless verbose.
    if not verbose:
        logging.getLogger().setLevel(logging.WARNING)

    console.print()

    # Resolve backup: use external path, resume existing, or create new
    own_backup = external_backup is None
    if external_backup is not None:
        if not backup_is_valid(external_backup):
            console.print(
                f"  [red]Invalid backup: {external_backup}[/]"
            )
            console.print(
                "  [dim]Expected BDMV/STREAM with .m2ts files[/]"
            )
            return
        backup_dir = external_backup
    else:
        # Check for existing backup before scanning the disc
        pending_backup = settings.staging_dir / ".backup"
        if backup_is_valid(pending_backup):
            try:
                answer = input(
                    "  Previous backup found. Reuse it? [Y/n]: "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer in ("", "y", "yes"):
                backup_dir = pending_backup
            else:
                backup_dir = None  # will create fresh below
        else:
            backup_dir = None

    # Scan from backup if available, otherwise from physical disc
    if backup_dir is not None:
        disc_info = _scan_disc(settings, backup_dir=backup_dir)
    else:
        disc_info = _scan_disc(settings)
    if disc_info is None:
        return

    # Create fresh backup if we don't have one yet
    if backup_dir is None:
        backup_dir = create_backup(settings, settings.staging_dir)

        # Re-scan from backup so title IDs match what makemkvcon
        # will use during remux.  Disc vs backup scans can assign
        # different indices to the same content.
        with console.status("  Indexing backup...", spinner="dots"):
            try:
                disc_info = scan_disc(settings, backup_dir=backup_dir)
            except Exception as e:
                console.print(f"  [red]Backup scan failed: {e}[/]")
                return
            classify_titles(
                disc_info.titles, settings.min_main_length
            )
            disc_info.detected_media_type = detect_media_type(
                disc_info.titles, settings.min_main_length
            )

    # Kick off TMDb lookup in background — will update disc_info
    # before the user finishes navigating prompts
    tmdb_thread = _start_tmdb_lookup(disc_info, settings)

    enrich_disc_info(disc_info, backup_dir, settings)

    # Notification dispatcher
    from ripper.notifications import (
        EventType,
        NotificationEvent,
        create_dispatcher,
    )

    dispatcher = create_dispatcher(settings)

    # Show enriched disc summary
    _show_disc_summary(disc_info, verbose=verbose)

    # Menu loop — flows return here on cancel/back.
    while True:
        if dispatcher.enabled:
            dispatcher.notify(NotificationEvent(
                event_type=EventType.ACTION_NEEDED,
                message="Ready — choose a rip mode",
                disc_name=disc_info.name,
            ))

        choice = _show_menu()
        if choice is None:
            break

        # Wait for TMDb metadata before any flow that needs a name
        if choice != 5:
            _await_tmdb(tmdb_thread)

        if choice == 0:
            _flow_movie(
                settings, disc_info, mode="full",
                backup_dir=backup_dir,
                dispatcher=dispatcher,
            )
        elif choice == 1:
            _flow_movie(
                settings, disc_info, mode="main",
                backup_dir=backup_dir,
                dispatcher=dispatcher,
            )
        elif choice == 2:
            _flow_movie(
                settings, disc_info, mode="multi",
                backup_dir=backup_dir,
                dispatcher=dispatcher,
            )
        elif choice == 3:
            _flow_tv(
                settings, disc_info,
                backup_dir=backup_dir,
                dispatcher=dispatcher,
            )
        elif choice == 4:
            _await_tmdb(tmdb_thread)
            _flow_select(
                settings, disc_info,
                backup_dir=backup_dir,
                dispatcher=dispatcher,
            )
        elif choice == 5:
            _show_disc_info(disc_info)

    # Prompt to clean up backup on normal exit.
    # Ctrl+C skips this, leaving the backup for next run.
    if own_backup and backup_is_valid(backup_dir):
        try:
            answer = input(
                "  Delete backup? [y/N]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if answer in ("y", "yes"):
            cleanup_backup(settings.staging_dir)
            console.print("  [dim]Backup removed.[/]")


# ── Scanning ─────────────────────────────────────────────────────────


def _scan_disc(
    settings: Settings, backup_dir: Path | None = None,
) -> DiscInfo | None:
    """Scan disc (or backup) with a live spinner, then classify."""
    label = "Scanning backup..." if backup_dir else "Scanning disc..."
    with console.status(f"  {label}", spinner="dots"):
        try:
            disc_info = scan_disc(settings, backup_dir=backup_dir)
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


def _show_disc_summary(
    disc_info: DiscInfo, verbose: bool = False,
) -> None:
    """Print enriched disc summary after backup + DiscDB."""
    if disc_info.discdb_title:
        console.print()
        console.print(
            f"  [green]TheDiscDB[/]: {disc_info.discdb_title}"
            f" ({disc_info.discdb_year or '?'})"
        )
    console.print()
    print_title_table(disc_info, show_source=verbose)
    console.print()


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
    if isinstance(idx, tuple):
        return idx[0]
    return idx


# ── Prompts ──────────────────────────────────────────────────────────


def _suggested_name(disc_info: DiscInfo) -> str:
    """Best movie name from DiscDB, TMDb, or disc name."""
    if disc_info.discdb_title and disc_info.discdb_year:
        raw = f"{disc_info.discdb_title} ({disc_info.discdb_year})"
    elif disc_info.discdb_title:
        raw = disc_info.discdb_title
    elif disc_info.tmdb_title and disc_info.year:
        raw = f"{disc_info.tmdb_title} ({disc_info.year})"
    elif disc_info.tmdb_title:
        raw = disc_info.tmdb_title
    else:
        raw = clean_disc_name(disc_info.name)
    return sanitize_filename(raw)


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


def _prompt_tv_info(
    suggested_show: str | None = None,
) -> tuple[str, int] | None:
    """Prompt for TV show name and season."""
    console.print()
    if suggested_show:
        prompt = f"  Show name [{suggested_show}] (b=back): "
    else:
        prompt = "  Show name (b=back): "
    try:
        show = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return None
    if _is_back_command(show):
        return None
    if not show:
        if suggested_show:
            show = suggested_show
        else:
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
        display = title_display_name(t)
        entries.append(
            f"{marker} {t.id:>2d}  {display[:35]:<35s}"
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

    rip_titles = _get_titles(disc_info, mode, selected_ids)
    rip_ids = {t.id for t in rip_titles}
    total_size = sum(t.size_bytes for t in rip_titles)
    total_dur = sum(t.duration_seconds for t in rip_titles)

    console.print()
    console.print(f"  [bold]Ready to rip: {name}[/]")
    console.print(f"  Mode: {mode_labels.get(mode, mode)}")
    console.print(
        f"  Titles: {len(rip_titles)}"
        f" | ~{fmt_size(total_size)}"
        f" | {fmt_duration(total_dur)}"
    )
    console.print()

    for t in disc_info.titles:
        selected = t.id in rip_ids
        marker = "*" if selected else " "
        display = title_display_name(t)
        if selected:
            console.print(
                f"   {marker} {t.id:>2d}  {display[:35]:<35s}  "
                f"{t.duration_display:>11s}  {t.size_display:>8s}"
            )
        else:
            console.print(
                f"   {marker} {t.id:>2d}  [dim]{display[:35]:<35s}  "
                f"{t.duration_display:>11s}  {t.size_display:>8s}[/]"
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
    if mode == "full":
        discdb = [t for t in disc_info.titles if t.discdb_info]
        if discdb:
            return discdb
    return disc_info.titles


# ── Disc Info ────────────────────────────────────────────────────────


def _show_disc_info(disc_info: DiscInfo) -> None:
    """Print a formatted title table."""
    console.print()
    if disc_info.discdb_title:
        console.print(
            f"  [green]TheDiscDB[/]: {disc_info.discdb_title}"
            f" ({disc_info.discdb_year or '?'})"
        )
    else:
        console.print("  [dim]TheDiscDB: no match[/]")
    console.print()
    print_title_table(disc_info)
    console.print()


# ── Rip Flows ────────────────────────────────────────────────────────
# Each flow returns normally when done or cancelled.
# The main loop always continues after a flow returns.


def _flow_movie(
    settings: Settings,
    disc_info: DiscInfo,
    mode: str,
    backup_dir: Path,
    dispatcher: NotificationDispatcher | None = None,
) -> None:
    """Movie rip flow (full, main, or multi)."""
    from ripper.notifications import EventType, NotificationEvent

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
            rip_movie_full(
                settings, disc_info, name, backup_dir,
                dispatcher=dispatcher,
            )
        elif mode == "main":
            rip_movie_main(settings, disc_info, name, backup_dir)
        elif mode == "multi":
            rip_multi_disc(
                settings, disc_info, name, disc_count, backup_dir,
                dispatcher=dispatcher,
            )
        if dispatcher and dispatcher.enabled:
            dispatcher.notify(NotificationEvent(
                event_type=EventType.RIP_COMPLETE,
                message=f"Rip complete: {name}",
                disc_name=disc_info.name,
            ))
    except RipCancelledError:
        console.print("\n  [yellow]Cancelled by user.[/]")
    except Exception as e:
        console.print(f"\n  [red]Error: {e}[/]")
        logger.error("Rip failed: %s", e, exc_info=True)
        if dispatcher and dispatcher.enabled:
            dispatcher.notify(NotificationEvent(
                event_type=EventType.RIP_FAILED,
                message=f"Rip failed: {name}",
                disc_name=disc_info.name,
            ))


def _flow_tv(
    settings: Settings,
    disc_info: DiscInfo,
    backup_dir: Path,
    dispatcher: NotificationDispatcher | None = None,
) -> None:
    """TV episode rip flow."""
    from ripper.notifications import EventType, NotificationEvent

    suggested_show = None
    if disc_info.discdb_media_type == MediaType.TV_SHOW:
        suggested_show = disc_info.discdb_title
    result = _prompt_tv_info(suggested_show=suggested_show)
    if result is None:
        return
    show, season = result

    if not _confirm_rip(disc_info, show, "tv", season_num=season):
        return

    try:
        rip_tv(settings, disc_info, show, season, backup_dir)
        if dispatcher and dispatcher.enabled:
            dispatcher.notify(NotificationEvent(
                event_type=EventType.RIP_COMPLETE,
                message=f"Rip complete: {show}",
                disc_name=disc_info.name,
            ))
    except RipCancelledError:
        console.print("\n  [yellow]Cancelled by user.[/]")
    except Exception as e:
        console.print(f"\n  [red]Error: {e}[/]")
        logger.error("Rip failed: %s", e, exc_info=True)
        if dispatcher and dispatcher.enabled:
            dispatcher.notify(NotificationEvent(
                event_type=EventType.RIP_FAILED,
                message=f"Rip failed: {show}",
                disc_name=disc_info.name,
            ))


def _flow_select(
    settings: Settings,
    disc_info: DiscInfo,
    backup_dir: Path,
    dispatcher: NotificationDispatcher | None = None,
) -> None:
    """Selected titles rip flow."""
    from ripper.notifications import EventType, NotificationEvent

    selected_ids = _select_titles(disc_info)
    if not selected_ids:
        return

    name = _suggested_name(disc_info)

    if not _confirm_rip(
        disc_info, name, "select", selected_ids=selected_ids
    ):
        return

    try:
        rip_selected(
            settings, disc_info, name, selected_ids, backup_dir
        )
        if dispatcher and dispatcher.enabled:
            dispatcher.notify(NotificationEvent(
                event_type=EventType.RIP_COMPLETE,
                message=f"Rip complete: {name}",
                disc_name=disc_info.name,
            ))
    except RipCancelledError:
        console.print("\n  [yellow]Cancelled by user.[/]")
    except Exception as e:
        console.print(f"\n  [red]Error: {e}[/]")
        logger.error("Rip failed: %s", e, exc_info=True)
        if dispatcher and dispatcher.enabled:
            dispatcher.notify(NotificationEvent(
                event_type=EventType.RIP_FAILED,
                message=f"Rip failed: {name}",
                disc_name=disc_info.name,
            ))


# ── Batch Mode ────────────────────────────────────────────────────


@dataclass
class _PendingDisc:
    """Tracks a disc whose remux is running in the background."""

    remux: RemuxHandle
    disc_info: DiscInfo
    name: str
    mode: str  # "full", "main", "tv", "select"
    backup_dir: Path
    # TV-specific
    show: str | None = None
    season: int | None = None
    # Select-specific
    selected_ids: set[int] | None = None


def _finish_pending_disc(
    settings: Settings,
    pending: _PendingDisc,
    dispatcher: NotificationDispatcher | None = None,
) -> None:
    """Complete post-remux work for a disc that was remuxed in background."""
    pending.remux.result_or_raise()

    if pending.mode == "full":
        classify_and_organize_movie(
            settings, pending.disc_info,
            pending.name, pending.remux.staging,
            dispatcher=dispatcher,
        )
    elif pending.mode == "main":
        console.print("  Organizing files...")
        organize_movie(
            pending.remux.staging, pending.name, settings,
        )
    elif pending.mode == "tv" and pending.show and pending.season:
        from ripper.core.organizer import find_mkv_files
        from ripper.tui.flows import _match_tv_episodes

        mkvs = find_mkv_files(pending.remux.staging)
        episode_map = _match_tv_episodes(
            settings, pending.disc_info,
            pending.show, pending.season, mkvs,
        )
        organize_tv(
            pending.remux.staging, pending.show,
            pending.season, episode_map, settings,
        )
    elif pending.mode == "select":
        # Selected titles don't need organize — they stay in staging
        pass

    console.print(
        f"  [green bold]Done![/] {pending.name}"
    )

    # Clean up the per-disc backup
    shutil.rmtree(pending.backup_dir, ignore_errors=True)


_INTERRUPT_ITEMS = [
    "Cancel current disc",
    "Skip to next disc",
    "Abort all",
    "Resume",
]


def _show_interrupt_menu() -> int | None:
    """Show interrupt menu when Ctrl+C is pressed. Returns index."""
    console.print()
    console.print("  [yellow bold]Interrupted — what do you want to do?[/]")
    console.print()

    menu = _build_terminal_menu(
        _INTERRUPT_ITEMS,
        title="  Select an action:",
        show_menu_entry_index=False,
        cycle_cursor=True,
        menu_cursor_style=("fg_yellow", "bold"),
        menu_highlight_style=("fg_yellow", "bold"),
    )
    idx = menu.show()
    console.print()

    if idx is None:
        return None
    if isinstance(idx, tuple):
        return idx[0]
    return idx


def _prompt_next_disc() -> bool:
    """Prompt to insert next disc. Returns False if user is done."""
    console.print()
    try:
        raw = input(
            "  Insert next disc and press Enter"
            " (or 'done' to finish): "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False
    return raw not in ("done", "d", "quit", "q")


def run_batch(
    settings: Settings,
    verbose: bool = False,
) -> None:
    """Batch-rip multiple discs with pipelined backup and remux.

    While disc N is being remuxed from its backup files, disc N+1 can
    be backed up from the optical drive concurrently.
    """
    if not verbose:
        logging.getLogger().setLevel(logging.WARNING)

    console.print()
    console.print(
        "  [bold]Batch mode[/]"
        " — pipeline backup and remux across discs"
    )
    console.print()

    from ripper.notifications import (
        EventType,
        NotificationEvent,
        create_dispatcher,
    )

    dispatcher = create_dispatcher(settings)

    pending: _PendingDisc | None = None
    disc_num = 0

    try:
        while True:
            disc_num += 1

            # Per-disc backup dir for isolation
            backup_dir_name = f".backup-disc{disc_num}"
            backup_staging = settings.staging_dir / backup_dir_name

            # If previous backup dir exists, clean it
            if backup_staging.exists():
                shutil.rmtree(backup_staging)

            # Scan + backup current disc
            # May overlap with a pending remux from previous disc
            disc_info = _scan_disc(settings)
            if disc_info is None:
                break

            # Backup — with concurrent progress if remux is active
            if pending and pending.remux.is_alive():
                console.print(
                    "  [dim]Backing up disc while"
                    " remuxing previous...[/]"
                )
                with ConcurrentProgress() as cp:
                    backup_dir = create_backup(
                        settings, backup_staging,
                        on_progress=cp.make_callback("backup"),
                        process_id=f"backup-disc{disc_num}",
                    )
            else:
                backup_dir = create_backup(settings, backup_staging)

            # Re-scan from backup so title IDs match what makemkvcon
            # will use during remux.  Disc vs backup scans can assign
            # different indices to the same content.
            disc_info = _scan_disc(settings, backup_dir=backup_dir)
            if disc_info is None:
                console.print(
                    "  [red]Backup scan failed, skipping disc[/]"
                )
                continue

            enrich_disc_info(disc_info, backup_dir, settings)

            # Wait for any pending remux before interactive prompts
            if pending:
                if pending.remux.is_alive():
                    console.print(
                        "  [dim]Waiting for previous"
                        " remux to finish...[/]"
                    )
                    pending.remux.join()
                try:
                    _finish_pending_disc(
                        settings, pending, dispatcher,
                    )
                except Exception as e:
                    console.print(
                        f"  [red]Previous disc error: {e}[/]"
                    )
                    logger.error(
                        "Post-remux failed: %s", e, exc_info=True,
                    )
                pending = None

            # Kick off TMDb in background
            tmdb_thread = _start_tmdb_lookup(disc_info, settings)

            # Interactive: show summary, menu, prompts
            _show_disc_summary(disc_info, verbose=verbose)

            if dispatcher.enabled:
                dispatcher.notify(NotificationEvent(
                    event_type=EventType.ACTION_NEEDED,
                    message=f"Disc {disc_num} ready — choose a rip mode",
                    disc_name=disc_info.name,
                ))

            choice = _show_menu()
            if choice is None:
                break

            if choice != 5:
                _await_tmdb(tmdb_thread)

            if choice == 5:
                _show_disc_info(disc_info)
                continue

            # Determine mode and gather params
            mode: str | None = None
            name: str | None = None
            show: str | None = None
            season: int | None = None
            disc_count: int = 1
            selected_ids: set[int] | None = None

            if choice == 0:
                mode = "full"
                name = _prompt_movie_name(disc_info)
            elif choice == 1:
                mode = "main"
                name = _prompt_movie_name(disc_info)
            elif choice == 2:
                # Multi-disc is its own pipeline, not batchable
                mode = "multi"
                name = _prompt_movie_name(disc_info)
                if name:
                    dc = _prompt_disc_count()
                    if dc is None:
                        continue
                    disc_count = dc
            elif choice == 3:
                mode = "tv"
                suggested_show = None
                if disc_info.discdb_media_type == MediaType.TV_SHOW:
                    suggested_show = disc_info.discdb_title
                result = _prompt_tv_info(
                    suggested_show=suggested_show,
                )
                if result is None:
                    continue
                show, season = result
                name = show
            elif choice == 4:
                mode = "select"
                selected_ids = _select_titles(disc_info)
                if not selected_ids:
                    continue
                name = _suggested_name(disc_info)

            if not name or not mode:
                continue

            # Confirm
            if mode == "tv":
                if not _confirm_rip(
                    disc_info, name, mode, season_num=season or 1,
                ):
                    continue
            elif mode == "select":
                if not _confirm_rip(
                    disc_info, name, mode,
                    selected_ids=selected_ids,
                ):
                    continue
            else:
                if not _confirm_rip(
                    disc_info, name, mode,
                    disc_count=disc_count,
                ):
                    continue

            # Multi-disc runs its own pipeline (not pipelined)
            if mode == "multi":
                try:
                    rip_multi_disc(
                        settings, disc_info, name,
                        disc_count, backup_dir,
                        dispatcher=dispatcher,
                    )
                except RipCancelledError:
                    console.print(
                        "\n  [yellow]Cancelled.[/]"
                    )
                except Exception as e:
                    console.print(f"\n  [red]Error: {e}[/]")
                    logger.error(
                        "Rip failed: %s", e, exc_info=True,
                    )
                shutil.rmtree(backup_dir, ignore_errors=True)

                if dispatcher.enabled:
                    dispatcher.notify(NotificationEvent(
                        event_type=EventType.INSERT_DISC,
                        message="Insert next disc",
                    ))
                if not _prompt_next_disc():
                    break
                continue

            # Start remux in background
            staging = settings.staging_dir / name
            titles = select_remux_titles(disc_info) if mode == "full" else None
            if mode == "main":
                titles = disc_info.main_titles
            elif mode == "select" and selected_ids:
                titles = [
                    t for t in disc_info.titles
                    if t.id in selected_ids
                ]

            remux_handle = start_remux_background(
                backup_dir, staging, name, settings,
                titles=titles,
                process_id=f"remux-disc{disc_num}",
            )

            pending = _PendingDisc(
                remux=remux_handle,
                disc_info=disc_info,
                name=name,
                mode=mode,
                backup_dir=backup_dir,
                show=show,
                season=season,
                selected_ids=selected_ids,
            )

            # Eject and prompt for next disc
            eject_disc(settings.device)

            if dispatcher.enabled:
                dispatcher.notify(NotificationEvent(
                    event_type=EventType.INSERT_DISC,
                    message="Insert next disc",
                    disc_name=name,
                ))
            if not _prompt_next_disc():
                # Finish the last disc
                console.print(
                    "  [dim]Finishing last disc...[/]"
                )
                pending.remux.join()
                try:
                    _finish_pending_disc(
                        settings, pending, dispatcher,
                    )
                except Exception as e:
                    console.print(
                        f"  [red]Error: {e}[/]"
                    )
                    logger.error(
                        "Post-remux failed: %s", e,
                        exc_info=True,
                    )
                pending = None
                break

            # Wait for disc to be ready
            console.print("  [dim]Waiting for disc...[/]")
            if not wait_for_disc(
                settings.device, timeout_seconds=120,
            ):
                console.print(
                    "  [red]Timed out waiting for disc[/]"
                )
                break

    except KeyboardInterrupt:
        from ripper.core.ripper import cancel_all_rips, cancel_rip

        action = _show_interrupt_menu()
        if action == 0:
            # Cancel current disc
            console.print("  [yellow]Cancelling current disc...[/]")
            cancel_rip(f"backup-disc{disc_num}")
            cancel_rip(f"remux-disc{disc_num}")
        elif action == 1:
            # Skip to next disc
            console.print("  [yellow]Skipping current disc...[/]")
            cancel_rip(f"backup-disc{disc_num}")
        elif action == 2:
            # Abort all
            console.print("  [yellow]Aborting all...[/]")
            cancel_all_rips()
            pending = None
        elif action == 3:
            # Resume — continue as if nothing happened
            console.print("  [dim]Resuming...[/]")
        else:
            # Menu escaped — treat as abort
            cancel_all_rips()
            pending = None
    finally:
        # Clean up any remaining pending disc
        if pending:
            if pending.remux.is_alive():
                console.print(
                    "  [dim]Waiting for pending remux...[/]"
                )
                pending.remux.join()
            try:
                _finish_pending_disc(
                    settings, pending, dispatcher,
                )
            except Exception as e:
                console.print(f"  [red]Cleanup error: {e}[/]")

    console.print()
    console.print("  [bold]Batch complete.[/]")
