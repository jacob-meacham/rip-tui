"""Rip flow operations for the interactive TUI."""

import asyncio
import logging
import shutil
from pathlib import Path

from rich.console import Console

from ripper.config.settings import Settings
from ripper.core.disc import DiscInfo, ExtraType, Title
from ripper.core.organizer import (
    find_mkv_files,
    organize_movie,
    organize_multi_disc,
    organize_tv,
)
from ripper.core.ripper import (
    backup_disc,
    remux_all_from_backup,
    remux_titles_from_backup,
)
from ripper.core.scanner import compute_hash_from_backup, scan_disc
from ripper.metadata.classifier import (
    apply_discdb_classifications,
    classify_titles,
    detect_media_type,
)
from ripper.tui.display import (
    classify_extras_interactive,
    print_progress,
    start_rip_with_status,
)
from ripper.utils.drive import eject_disc, wait_for_disc
from ripper.utils.matching import find_title_for_mkv, match_title_id

logger = logging.getLogger(__name__)

console = Console()


# ── Backup Pipeline ────────────────────────────────────────────────


def create_backup(settings: Settings, staging_dir: Path) -> Path:
    """Backup disc to staging_dir/.backup, returns backup dir path."""
    backup_dir = staging_dir / ".backup"

    # Clean up any partial/corrupt leftover backup
    if backup_dir.exists():
        shutil.rmtree(backup_dir)

    staging_dir.mkdir(parents=True, exist_ok=True)

    start_rip_with_status(
        "Backing up disc...",
        backup_disc,
        backup_dir,
        settings,
        on_progress=print_progress,
    )

    return backup_dir


def backup_is_valid(backup_dir: Path) -> bool:
    """Check if a backup directory has BDMV/STREAM with M2TS files."""
    stream_dir = backup_dir / "BDMV" / "STREAM"
    if not stream_dir.is_dir():
        return False
    return any(stream_dir.glob("*.m2ts"))


def enrich_disc_info(
    disc_info: DiscInfo,
    backup_dir: Path,
    settings: Settings,
) -> None:
    """Compute content hash from backup and do DiscDB lookup.

    Skips DiscDB API call when discdb_enabled is False,
    but always computes the content hash from the backup.
    """
    content_hash = compute_hash_from_backup(backup_dir)
    if content_hash:
        disc_info.content_hash = content_hash
        console.print(f"  Content hash: [dim]{content_hash}[/]")
    else:
        console.print("  [dim]Could not compute content hash[/]")
        return

    if not settings.discdb_enabled:
        return

    result = _sync_discdb_lookup(content_hash)
    if result:
        disc_info.discdb_title = result.get("title")
        year = result.get("year")
        if year is not None:
            disc_info.discdb_year = int(year)
        discdb_type = result.get("type", "")
        disc_info.discdb_media_type = detect_media_type(
            disc_info.titles, discdb_type=discdb_type
        )
        apply_discdb_classifications(
            disc_info.titles, result.get("titles", [])
        )
        console.print(
            f"  [green]TheDiscDB[/]: {disc_info.discdb_title}"
            f" ({disc_info.discdb_year or '?'})"
        )
    else:
        console.print("  [dim]TheDiscDB: no match[/]")


def remux_from_backup(
    backup_dir: Path,
    staging: Path,
    label: str,
    settings: Settings,
    titles: list[Title] | None = None,
) -> None:
    """Remux all or specific titles from backup to staging dir."""
    if titles is not None:
        start_rip_with_status(
            f"Remuxing: {label}",
            remux_titles_from_backup,
            backup_dir,
            titles,
            staging,
            settings,
            on_progress=print_progress,
        )
    else:
        start_rip_with_status(
            f"Remuxing: {label}",
            remux_all_from_backup,
            backup_dir,
            staging,
            settings,
            on_progress=print_progress,
        )


def cleanup_backup(staging_dir: Path) -> None:
    """Remove staging_dir/.backup, handles missing dir gracefully."""
    backup_dir = staging_dir / ".backup"
    shutil.rmtree(backup_dir, ignore_errors=True)


def _sync_discdb_lookup(content_hash: str) -> dict | None:
    """Synchronous DiscDB lookup."""
    from ripper.metadata.discdb import DiscDbClient

    async def _lookup() -> dict | None:
        client = DiscDbClient()
        try:
            return await client.lookup_disc(content_hash)
        finally:
            await client.close()

    try:
        return asyncio.run(_lookup())
    except Exception:
        logger.warning("DiscDB lookup failed", exc_info=True)
        return None


# ── Rip Flows ──────────────────────────────────────────────────────


def _match_mkvs_to_titles(
    mkvs: list[Path], titles: list[Title],
) -> dict[Path, Title]:
    """Match MKV files to disc titles by tXX pattern in filename."""
    result: dict[Path, Title] = {}
    for mkv in mkvs:
        title = find_title_for_mkv(mkv, titles)
        if title:
            result[mkv] = title
    return result


def rip_movie_full(
    settings: Settings,
    disc_info: DiscInfo,
    name: str,
    backup_dir: Path,
) -> None:
    """Rip movie with all extras."""
    staging = settings.staging_dir / name

    # When DiscDB data exists, remux only known titles
    discdb_titles = [t for t in disc_info.titles if t.discdb_info]
    if discdb_titles:
        remux_from_backup(
            backup_dir, staging, name, settings, titles=discdb_titles,
        )
    else:
        remux_from_backup(backup_dir, staging, name, settings)

    mkvs = find_mkv_files(staging)

    main_mkv = None
    extras_map: dict[Path, ExtraType] = {}
    names_map: dict[Path, str] = {}

    if discdb_titles:
        mkv_map = _match_mkvs_to_titles(mkvs, disc_info.titles)
        for mkv, title in mkv_map.items():
            if title.is_main_feature:
                main_mkv = mkv
            else:
                extras_map[mkv] = (
                    title.suggested_extra_type or ExtraType.EXTRAS
                )
                if title.discdb_info and title.discdb_info.item_title:
                    names_map[mkv] = title.discdb_info.item_title

        # Interactive classification for any unmatched MKVs
        unmatched = [
            m for m in mkvs
            if m not in mkv_map and m != main_mkv
        ]
        if unmatched:
            manual = classify_extras_interactive(
                unmatched, disc_info=disc_info,
            )
            extras_map.update(manual)
    else:
        # No DiscDB — interactive classification for all extras
        extras = mkvs[1:]
        if extras:
            extras_map = classify_extras_interactive(
                extras, disc_info=disc_info,
            )

    console.print("  Organizing files...")
    organize_movie(
        staging, name, settings,
        extras_map=extras_map,
        main_mkv=main_mkv,
        names_map=names_map,
    )

    if settings.auto_eject:
        eject_disc(settings.device)

    console.print(
        f"  [green bold]Done![/]"
        f" Output: {settings.movies_dir / name}"
    )


def rip_movie_main(
    settings: Settings,
    disc_info: DiscInfo,
    name: str,
    backup_dir: Path,
) -> None:
    """Rip main feature only."""
    staging = settings.staging_dir / name
    main_titles = disc_info.main_titles
    if not main_titles:
        console.print("  [red]No main feature detected[/]")
        return

    remux_from_backup(
        backup_dir, staging, name, settings, titles=main_titles
    )

    console.print("  Organizing files...")
    organize_movie(staging, name, settings)

    if settings.auto_eject:
        eject_disc(settings.device)

    console.print(
        f"  [green bold]Done![/]"
        f" Output: {settings.movies_dir / name}"
    )


def rip_multi_disc(
    settings: Settings,
    disc_info: DiscInfo,
    name: str,
    disc_count: int,
    backup_dir: Path,
    merge: bool = True,
) -> None:
    """Rip multi-disc movie.

    Uses the existing backup for disc 1. For discs 2+: eject, wait,
    scan, backup, enrich, remux, cleanup per disc.
    """
    disc_dirs: list[Path] = []

    for d in range(1, disc_count + 1):
        if d == 1:
            # Disc 1: remux from the already-created backup
            disc_staging = settings.staging_dir / f"{name}-disc{d}"
            remux_from_backup(
                backup_dir,
                disc_staging,
                f"disc {d}/{disc_count}",
                settings,
            )
            disc_dirs.append(disc_staging)
        else:
            eject_disc(settings.device)
            console.print()
            try:
                input(f"  Insert disc {d} and press Enter...")
            except (EOFError, KeyboardInterrupt):
                console.print("\n  [yellow]Cancelled.[/]")
                return
            console.print("  [dim]Waiting for disc...[/]")
            if not wait_for_disc(
                settings.device, timeout_seconds=120
            ):
                console.print(
                    f"  [red]Timed out waiting for disc {d}[/]"
                )
                return

            # Scan new disc
            next_disc = scan_disc(settings)
            classify_titles(
                next_disc.titles, settings.min_main_length
            )

            # Backup, enrich, remux, cleanup for this disc
            disc_staging = settings.staging_dir / f"{name}-disc{d}"
            next_backup = create_backup(settings, disc_staging)
            enrich_disc_info(next_disc, next_backup, settings)

            remux_from_backup(
                next_backup,
                disc_staging,
                f"disc {d}/{disc_count}",
                settings,
            )
            cleanup_backup(disc_staging)
            disc_dirs.append(disc_staging)

    console.print("  Organizing and merging files...")
    organize_multi_disc(disc_dirs, name, settings, merge=merge)

    if settings.auto_eject:
        eject_disc(settings.device)

    console.print(
        f"  [green bold]Done![/]"
        f" Output: {settings.movies_dir / name}"
    )


def rip_tv(
    settings: Settings,
    disc_info: DiscInfo,
    show: str,
    season: int,
    backup_dir: Path,
) -> None:
    """Rip TV episodes."""
    staging = settings.staging_dir / f"{show}-S{season:02d}"

    remux_from_backup(
        backup_dir, staging, f"{show} Season {season}", settings
    )

    console.print("  Organizing episodes...")
    mkvs = find_mkv_files(staging)
    episode_map = _match_tv_episodes(
        settings, disc_info, show, season, mkvs
    )

    season_dir = organize_tv(
        staging, show, season, episode_map, settings
    )

    if settings.auto_eject:
        eject_disc(settings.device)

    console.print(f"  [green bold]Done![/] Output: {season_dir}")


def rip_selected(
    settings: Settings,
    disc_info: DiscInfo,
    name: str,
    selected_ids: set[int],
    backup_dir: Path,
) -> None:
    """Rip selected titles only."""
    selected = [
        t
        for t in disc_info.titles
        if t.id in selected_ids
    ]
    remux_from_backup(
        backup_dir,
        settings.staging_dir / name,
        name,
        settings,
        titles=selected,
    )
    console.print(
        f"  [green bold]Done![/]"
        f" Output: {settings.staging_dir / name}"
    )


# ── TV Episode Matching ─────────────────────────────────────────────


def _match_tv_episodes(
    settings: Settings,
    disc_info: DiscInfo,
    show: str,
    season: int,
    mkvs: list[Path],
) -> dict[Path, int]:
    """Match MKV files to episode numbers."""
    # Try DiscDB episode data first
    discdb_result = _try_discdb_episode_match(disc_info, season, mkvs)
    if discdb_result:
        return discdb_result

    # Then TMDb duration matching
    if settings.tmdb_api_key:
        try:
            result = _try_tmdb_episode_match(
                settings, disc_info, show, season, mkvs
            )
            if result:
                return result
        except Exception:
            logger.warning(
                "TMDb episode match failed,"
                " using size-based mapping"
            )

    # Sequential fallback
    return {mkv: i + 1 for i, mkv in enumerate(mkvs)}


def _try_discdb_episode_match(
    disc_info: DiscInfo,
    season: int,
    mkvs: list[Path],
) -> dict[Path, int] | None:
    """Try to match episodes using TheDiscDB data.

    Returns episode map or None if no DiscDB episode data exists.
    """
    # Build mapping from title ID to episode number
    episode_titles: dict[int, int] = {}
    for title in disc_info.titles:
        if (
            title.discdb_info
            and title.discdb_info.item_type == "Episode"
            and title.discdb_info.season == season
            and title.discdb_info.episode is not None
        ):
            episode_titles[title.id] = title.discdb_info.episode

    if not episode_titles:
        return None

    # Match MKV files to title IDs via filename patterns
    episode_map: dict[Path, int] = {}
    for mkv in mkvs:
        for title_id, ep_num in episode_titles.items():
            if match_title_id(mkv.stem, title_id):
                episode_map[mkv] = ep_num
                break

    # Fill unmatched files with sequential episode numbers
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


def _try_tmdb_episode_match(
    settings: Settings,
    disc_info: DiscInfo,
    show: str,
    season: int,
    mkvs: list[Path],
) -> dict[Path, int] | None:
    """Try to match episodes using TMDb runtimes."""
    from ripper.metadata.matcher import (
        match_episodes_by_duration,
        match_title,
    )
    from ripper.metadata.tmdb import TMDbClient

    async def _lookup():
        client = TMDbClient(settings.tmdb_api_key)
        try:
            results = await client.search_tv(show)
            match = match_title(
                show,
                results,
                title_key="name",
                threshold=settings.fuzzy_threshold,
            )
            if not match:
                return None
            tv_id = match.get("id")
            if not tv_id:
                return None
            return await client.get_season_episodes(
                tv_id, season
            )
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

    matches = match_episodes_by_duration(
        title_durations, episode_runtimes
    )
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


def _get_mkv_durations(
    disc_info: DiscInfo, mkvs: list[Path]
) -> list[tuple[int, int]]:
    """Get durations for MKV files from disc_info."""
    durations: list[tuple[int, int]] = []
    for i, mkv in enumerate(mkvs):
        title = find_title_for_mkv(mkv, disc_info.titles)
        dur = title.duration_seconds if title else 0
        durations.append((i, dur))
    return durations
