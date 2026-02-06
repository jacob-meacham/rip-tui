"""Inline interactive CLI for ripping discs."""

import asyncio
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ripper.config.settings import Settings
from ripper.core.disc import DiscInfo, ExtraType, MediaType
from ripper.core.organizer import organize_movie, organize_multi_disc, organize_tv
from ripper.core.ripper import (
    RipCancelledError,
    RipProgress,
    rip_all_titles,
    rip_titles,
)
from ripper.core.scanner import scan_disc
from ripper.metadata.classifier import (
    classify_extra,
    classify_titles,
    detect_media_type,
)
from ripper.metadata.matcher import clean_disc_name
from ripper.utils.drive import eject_disc, wait_for_disc
from ripper.utils.formatting import fmt_duration, fmt_size

logger = logging.getLogger(__name__)

console = Console()


def run_interactive(settings: Settings) -> None:
    """Main interactive CLI flow."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    console.print()

    # Scan
    disc_info = _scan_disc(settings)
    if disc_info is None:
        return

    # Menu loop
    while True:
        choice = _show_menu()

        if choice == "1":
            _flow_movie(settings, disc_info, mode="full")
            break
        elif choice == "2":
            _flow_movie(settings, disc_info, mode="main")
            break
        elif choice == "3":
            _flow_movie(settings, disc_info, mode="multi")
            break
        elif choice == "4":
            _flow_tv(settings, disc_info)
            break
        elif choice == "5":
            _flow_select(settings, disc_info)
            break
        elif choice == "6":
            _show_disc_info(disc_info)
            # After viewing info, show menu again
        elif choice in ("q", ""):
            return
        else:
            console.print("  [red]Invalid choice[/]")


# ── Scanning ─────────────────────────────────────────────────────────


def _scan_disc(settings: Settings) -> DiscInfo | None:
    """Scan disc and print summary."""
    console.print("  [dim]Scanning disc...[/]")
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

    console.print(f"  [dim]{disc_info.name}[/]")
    console.print(f"  [bold]{cleaned}[/] — {media_label}")
    console.print(f"  [dim]{total} titles ({main_count} main, {extra_count} extras)[/]")

    # TMDb lookup
    if settings.auto_lookup and settings.tmdb_api_key:
        _do_tmdb_lookup(disc_info, cleaned, settings)

    console.print()
    return disc_info


def _do_tmdb_lookup(disc_info: DiscInfo, cleaned_name: str, settings: Settings) -> None:
    """Look up title on TMDb and update disc_info."""
    from ripper.metadata.matcher import match_title
    from ripper.metadata.tmdb import TMDbClient

    async def _lookup() -> dict | None:
        client = TMDbClient(settings.tmdb_api_key)
        try:
            results = await client.search_movie(cleaned_name)
            return match_title(
                cleaned_name, results, threshold=settings.fuzzy_threshold
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
        display = f"{title} ({year})" if year else title
        console.print(f"  [green]{display}[/] [dim](via TMDb)[/]")


# ── Menu ─────────────────────────────────────────────────────────────


def _show_menu() -> str:
    """Show action menu and return user choice."""
    console.print("  [bold]What do you want to rip?[/]")
    console.print()
    console.print("  [cyan]1[/]  Movie with extras")
    console.print("  [cyan]2[/]  Main feature only")
    console.print("  [cyan]3[/]  Multi-disc movie")
    console.print("  [cyan]4[/]  TV episodes")
    console.print("  [cyan]5[/]  Select specific titles")
    console.print("  [cyan]6[/]  View disc info")
    console.print("  [dim]q  Quit[/]")
    console.print()
    try:
        return input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


# ── Prompts ──────────────────────────────────────────────────────────


def _suggested_name(disc_info: DiscInfo) -> str:
    """Best movie name from TMDb or disc name."""
    if disc_info.tmdb_title and disc_info.year:
        return f"{disc_info.tmdb_title} ({disc_info.year})"
    if disc_info.tmdb_title:
        return disc_info.tmdb_title
    return clean_disc_name(disc_info.name)


def _prompt_movie_name(disc_info: DiscInfo) -> str | None:
    """Prompt for movie name with a suggested default."""
    suggested = _suggested_name(disc_info)
    console.print()
    try:
        name = input(f"  Movie name [{suggested}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return name or suggested


def _prompt_disc_count() -> int | None:
    """Prompt for number of discs."""
    try:
        raw = input("  Number of discs [2]: ").strip()
    except (EOFError, KeyboardInterrupt):
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
        show = input("  Show name: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not show:
        console.print("  [red]Show name cannot be empty[/]")
        return None

    try:
        raw = input("  Season number [1]: ").strip()
    except (EOFError, KeyboardInterrupt):
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
    """Show title list and let user select by ID."""
    console.print()
    _print_title_table(disc_info)
    console.print()

    # Pre-select main features
    main_ids = {t.id for t in disc_info.main_titles}
    default = ",".join(str(i) for i in sorted(main_ids))

    console.print("  Enter title IDs (comma-separated), 'all', or 'q' to cancel.")
    try:
        raw = input(f"  Select [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if raw.lower() == "q":
        return None
    if raw.lower() == "all":
        return {t.id for t in disc_info.titles}
    if not raw:
        return main_ids

    try:
        ids = {int(x.strip()) for x in raw.split(",")}
    except ValueError:
        console.print("  [red]Invalid input — use comma-separated numbers[/]")
        return None

    valid_ids = {t.id for t in disc_info.titles}
    invalid = ids - valid_ids
    if invalid:
        console.print(f"  [red]Unknown title IDs: {invalid}[/]")
        return None

    return ids


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
        f"  Titles: {len(titles)} | ~{fmt_size(total_size)} | {fmt_duration(total_dur)}"
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
        answer = input("  Start rip? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("", "y", "yes")


def _get_titles(disc_info, mode, selected_ids=None):
    """Get the list of titles for a given mode."""
    if mode == "main":
        return disc_info.main_titles
    if selected_ids:
        return [t for t in disc_info.titles if t.id in selected_ids]
    return disc_info.titles


# ── Disc Info ────────────────────────────────────────────────────────


def _show_disc_info(disc_info: DiscInfo) -> None:
    """Print a formatted title table."""
    console.print()
    _print_title_table(disc_info)
    console.print()


def _print_title_table(disc_info: DiscInfo) -> None:
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


# ── Progress ─────────────────────────────────────────────────────────

# Width of the progress bar in characters
_BAR_WIDTH = 30


def _print_progress(progress: RipProgress) -> None:
    """Print single-line progress update with carriage return."""
    pct = progress.percent
    filled = int(_BAR_WIDTH * pct / 100)
    bar = "\u2588" * filled + "\u2591" * (_BAR_WIDTH - filled)

    parts = [
        f"\r  {bar}  {pct:5.1f}%",
        f"  {fmt_size(progress.current_bytes)} / {fmt_size(progress.total_bytes)}",
    ]
    if progress.eta_seconds is not None:
        parts.append(f"  ETA: {fmt_duration(progress.eta_seconds)}")

    line = "".join(parts)
    # Pad to overwrite previous longer lines
    sys.stdout.write(f"{line:<100s}")
    sys.stdout.flush()


# ── Extras Classification ────────────────────────────────────────────


def _classify_extras(extras: list[Path]) -> dict[Path, ExtraType]:
    """Interactive extras classification prompt."""
    classifications: dict[Path, ExtraType] = {}

    console.print()
    console.print("  [bold]Classify extras for Emby:[/]")
    console.print()

    for i, path in enumerate(extras, 1):
        size = path.stat().st_size if path.exists() else 0
        suggested = classify_extra(path.stem)
        classifications[path] = suggested
        console.print(
            f"  [cyan]{i:>2d}[/]  {path.name[:40]:<40s}  "
            f"{fmt_size(size):>8s}  [dim][{suggested.value}][/]"
        )

    console.print()
    console.print("  [dim]Change: '<number> <category>' (e.g. '1 featurettes')[/]")
    console.print("  [dim]Categories: extras, behind the scenes, deleted scenes,[/]")
    console.print("  [dim]  featurettes, interviews, scenes, shorts, trailers[/]")
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
            console.print(f"  [red]Number must be 1-{len(extras_list)}[/]")
            continue

        category = parts[1].lower()
        if category not in valid_types:
            console.print(f"  [red]Unknown category: {category}[/]")
            continue

        path = extras_list[idx - 1]
        classifications[path] = valid_types[category]
        console.print(f"  [green]{idx} -> {category}[/]")

    return classifications


# ── Rip Flows ────────────────────────────────────────────────────────


def _flow_movie(settings: Settings, disc_info: DiscInfo, mode: str) -> None:
    """Movie rip flow (full, main, or multi)."""
    name = _prompt_movie_name(disc_info)
    if not name:
        return

    disc_count = 1
    if mode == "multi":
        disc_count = _prompt_disc_count()
        if disc_count is None:
            return

    if not _confirm_rip(disc_info, name, mode, disc_count=disc_count):
        console.print("  Cancelled.")
        return

    try:
        if mode == "full":
            _rip_movie_full(settings, disc_info, name)
        elif mode == "main":
            _rip_movie_main(settings, disc_info, name)
        elif mode == "multi":
            _rip_multi_disc(settings, disc_info, name, disc_count)
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
        console.print("  Cancelled.")
        return

    try:
        _rip_tv(settings, disc_info, show, season)
    except RipCancelledError:
        console.print("\n  [yellow]Cancelled by user.[/]")
    except Exception as e:
        console.print(f"\n  [red]Error: {e}[/]")
        logger.error("Rip failed: %s", e, exc_info=True)


def _flow_select(settings: Settings, disc_info: DiscInfo) -> None:
    """Selected titles rip flow."""
    selected_ids = _select_titles(disc_info)
    if not selected_ids:
        return

    name = _suggested_name(disc_info)

    if not _confirm_rip(disc_info, name, "select", selected_ids=selected_ids):
        console.print("  Cancelled.")
        return

    staging = settings.staging_dir / name
    console.print()
    console.print(f"  [bold]Ripping to {staging}[/]")

    try:
        selected = [t for t in disc_info.titles if t.id in selected_ids]
        rip_titles(selected, staging, settings, on_progress=_print_progress)
        console.print(f"\n  [green bold]Done![/] Output: {staging}")
    except RipCancelledError:
        console.print("\n  [yellow]Cancelled by user.[/]")
    except Exception as e:
        console.print(f"\n  [red]Error: {e}[/]")
        logger.error("Rip failed: %s", e, exc_info=True)


# ── Rip Operations ───────────────────────────────────────────────────


def _rip_movie_full(settings: Settings, disc_info: DiscInfo, name: str) -> None:
    """Rip movie with all extras."""
    staging = settings.staging_dir / name

    console.print()
    console.print(f"  [bold]Ripping: {name}[/]")
    rip_all_titles(staging, settings, on_progress=_print_progress)
    console.print()

    # Classify extras
    mkvs = sorted(staging.glob("*.mkv"), key=lambda p: p.stat().st_size, reverse=True)
    extras = mkvs[1:]
    extras_map = _classify_extras(extras) if extras else None

    console.print("  Organizing files...")
    organize_movie(staging, name, settings, extras_map=extras_map)

    if settings.auto_eject:
        eject_disc(settings.device)

    console.print(f"  [green bold]Done![/] Output: {settings.movies_dir / name}")


def _rip_movie_main(settings: Settings, disc_info: DiscInfo, name: str) -> None:
    """Rip main feature only."""
    staging = settings.staging_dir / name
    main_titles = disc_info.main_titles
    if not main_titles:
        console.print("  [red]No main feature detected[/]")
        return

    console.print()
    console.print(f"  [bold]Ripping: {name}[/]")
    rip_titles(main_titles, staging, settings, on_progress=_print_progress)
    console.print()

    console.print("  Organizing files...")
    organize_movie(staging, name, settings)

    if settings.auto_eject:
        eject_disc(settings.device)

    console.print(f"  [green bold]Done![/] Output: {settings.movies_dir / name}")


def _rip_multi_disc(
    settings: Settings, disc_info: DiscInfo, name: str, disc_count: int
) -> None:
    """Rip multi-disc movie."""
    disc_dirs: list[Path] = []

    for d in range(1, disc_count + 1):
        if d > 1:
            eject_disc(settings.device)
            console.print()
            try:
                input(f"  Insert disc {d} and press Enter...")
            except (EOFError, KeyboardInterrupt):
                console.print("\n  [yellow]Cancelled.[/]")
                return
            console.print("  [dim]Waiting for disc...[/]")
            if not wait_for_disc(settings.device, timeout_seconds=120):
                console.print(f"  [red]Timed out waiting for disc {d}[/]")
                return

        disc_staging = settings.staging_dir / f"{name}-disc{d}"
        console.print()
        console.print(f"  [bold]Ripping disc {d}/{disc_count}...[/]")
        rip_all_titles(disc_staging, settings, on_progress=_print_progress)
        disc_dirs.append(disc_staging)

    console.print()
    console.print("  Organizing and merging files...")
    organize_multi_disc(disc_dirs, name, settings)

    if settings.auto_eject:
        eject_disc(settings.device)

    console.print(f"  [green bold]Done![/] Output: {settings.movies_dir / name}")


def _rip_tv(settings: Settings, disc_info: DiscInfo, show: str, season: int) -> None:
    """Rip TV episodes."""
    staging = settings.staging_dir / f"{show}-S{season:02d}"

    console.print()
    console.print(f"  [bold]Ripping: {show} Season {season}[/]")
    rip_all_titles(staging, settings, on_progress=_print_progress)
    console.print()

    console.print("  Organizing episodes...")
    mkvs = sorted(staging.glob("*.mkv"), key=lambda p: p.stat().st_size, reverse=True)
    episode_map = _match_tv_episodes(settings, disc_info, show, season, mkvs)

    season_dir = organize_tv(staging, show, season, episode_map, settings)

    if settings.auto_eject:
        eject_disc(settings.device)

    console.print(f"  [green bold]Done![/] Output: {season_dir}")


def _match_tv_episodes(
    settings: Settings,
    disc_info: DiscInfo,
    show: str,
    season: int,
    mkvs: list[Path],
) -> dict[Path, int]:
    """Match MKV files to episode numbers via TMDb or size fallback."""
    if settings.tmdb_api_key:
        try:
            result = _try_tmdb_episode_match(settings, disc_info, show, season, mkvs)
            if result:
                return result
        except Exception:
            logger.warning("TMDb episode match failed, using size-based mapping")

    return {mkv: i + 1 for i, mkv in enumerate(mkvs)}


def _try_tmdb_episode_match(
    settings: Settings,
    disc_info: DiscInfo,
    show: str,
    season: int,
    mkvs: list[Path],
) -> dict[Path, int] | None:
    """Try to match episodes using TMDb runtimes."""
    from ripper.metadata.matcher import match_episodes_by_duration, match_title
    from ripper.metadata.tmdb import TMDbClient

    async def _lookup():
        client = TMDbClient(settings.tmdb_api_key)
        try:
            results = await client.search_tv(show)
            match = match_title(
                show, results, title_key="name", threshold=settings.fuzzy_threshold
            )
            if not match:
                return None
            tv_id = match.get("id")
            if not tv_id:
                return None
            return await client.get_season_episodes(tv_id, season)
        finally:
            await client.close()

    episodes = asyncio.run(_lookup())
    if not episodes:
        return None

    title_durations = _get_mkv_durations(disc_info, mkvs)
    episode_runtimes: list[tuple[int, int]] = [
        (ep["episode_number"], ep.get("runtime", 0) * 60)
        for ep in episodes
        if ep.get("runtime")
    ]

    if not episode_runtimes:
        return None

    matches = match_episodes_by_duration(title_durations, episode_runtimes)
    if not matches:
        return None

    episode_map: dict[Path, int] = {}
    for idx, ep_num in matches.items():
        if idx < len(mkvs):
            episode_map[mkvs[idx]] = ep_num

    # Fill unmatched with next available
    used_eps = set(episode_map.values())
    next_ep = 1
    for mkv in mkvs:
        if mkv not in episode_map:
            while next_ep in used_eps:
                next_ep += 1
            episode_map[mkv] = next_ep
            used_eps.add(next_ep)
            next_ep += 1

    return episode_map


def _get_mkv_durations(disc_info: DiscInfo, mkvs: list[Path]) -> list[tuple[int, int]]:
    """Get durations for MKV files from disc_info."""
    durations: list[tuple[int, int]] = []
    for i, mkv in enumerate(mkvs):
        dur = 0
        stem = mkv.stem.lower()
        for title in disc_info.titles:
            patterns = [
                f"t{title.id:02d}",
                f"title{title.id:02d}",
                f"title_{title.id}",
            ]
            if any(p in stem for p in patterns):
                dur = title.duration_seconds
                break
        durations.append((i, dur))
    return durations
