"""Rip flow operations for the interactive TUI."""

import asyncio
import logging
from pathlib import Path

from rich.console import Console

from ripper.config.settings import Settings
from ripper.core.disc import DiscInfo
from ripper.core.organizer import (
    organize_movie,
    organize_multi_disc,
    organize_tv,
)
from ripper.core.ripper import rip_all_titles, rip_titles
from ripper.tui.display import (
    classify_extras_interactive,
    print_progress,
    start_rip_with_status,
)
from ripper.utils.drive import eject_disc, wait_for_disc

logger = logging.getLogger(__name__)

console = Console()


def rip_movie_full(
    settings: Settings, disc_info: DiscInfo, name: str
) -> None:
    """Rip movie with all extras."""
    staging = settings.staging_dir / name

    start_rip_with_status(
        f"Ripping: {name}",
        rip_all_titles,
        staging,
        settings,
        on_progress=print_progress,
    )

    # Classify extras
    mkvs = sorted(
        staging.glob("*.mkv"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    extras = mkvs[1:]
    extras_map = classify_extras_interactive(extras) if extras else None

    console.print("  Organizing files...")
    organize_movie(staging, name, settings, extras_map=extras_map)

    if settings.auto_eject:
        eject_disc(settings.device)

    console.print(
        f"  [green bold]Done![/]"
        f" Output: {settings.movies_dir / name}"
    )


def rip_movie_main(
    settings: Settings, disc_info: DiscInfo, name: str
) -> None:
    """Rip main feature only."""
    staging = settings.staging_dir / name
    main_titles = disc_info.main_titles
    if not main_titles:
        console.print("  [red]No main feature detected[/]")
        return

    start_rip_with_status(
        f"Ripping: {name}",
        rip_titles,
        main_titles,
        staging,
        settings,
        on_progress=print_progress,
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
            if not wait_for_disc(
                settings.device, timeout_seconds=120
            ):
                console.print(
                    f"  [red]Timed out waiting for disc {d}[/]"
                )
                return

        disc_staging = settings.staging_dir / f"{name}-disc{d}"
        start_rip_with_status(
            f"Ripping disc {d}/{disc_count}...",
            rip_all_titles,
            disc_staging,
            settings,
            on_progress=print_progress,
        )
        disc_dirs.append(disc_staging)

    console.print("  Organizing and merging files...")
    organize_multi_disc(disc_dirs, name, settings)

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
) -> None:
    """Rip TV episodes."""
    staging = settings.staging_dir / f"{show}-S{season:02d}"

    start_rip_with_status(
        f"Ripping: {show} Season {season}",
        rip_all_titles,
        staging,
        settings,
        on_progress=print_progress,
    )

    console.print("  Organizing episodes...")
    mkvs = sorted(
        staging.glob("*.mkv"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
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
) -> None:
    """Rip selected titles only."""
    selected = [
        t
        for t in disc_info.titles
        if t.id in selected_ids
    ]
    start_rip_with_status(
        f"Ripping to {settings.staging_dir / name}",
        rip_titles,
        selected,
        settings.staging_dir / name,
        settings,
        on_progress=print_progress,
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

    return {mkv: i + 1 for i, mkv in enumerate(mkvs)}


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
