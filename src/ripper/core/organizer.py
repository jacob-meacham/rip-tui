"""File organization for Emby-compatible folder structures."""

import logging
import shutil
import subprocess
from pathlib import Path

from ripper.config.settings import Settings
from ripper.core.disc import ExtraType

logger = logging.getLogger(__name__)


def organize_movie(
    staging_dir: Path,
    movie_name: str,
    settings: Settings,
    extras_map: dict[Path, ExtraType] | None = None,
) -> Path:
    """Organize ripped files into Emby movie folder structure.

    Args:
        staging_dir: Directory containing ripped MKV files.
        movie_name: Movie name with year, e.g. "Dune (2021)".
        settings: Application settings.
        extras_map: Optional mapping of file paths to extra types.
            Files not in this map and not the main feature go to 'extras/'.

    Returns:
        Path to the organized movie directory.
    """
    dest = settings.movies_dir / movie_name
    dest.mkdir(parents=True, exist_ok=True)

    mkvs = sorted(
        staging_dir.glob("*.mkv"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if not mkvs:
        raise FileNotFoundError(f"No MKV files found in {staging_dir}")

    # Largest file is the main feature
    main_mkv = mkvs[0]
    main_dest = dest / f"{movie_name}.mkv"
    logger.info("Main feature: %s -> %s", main_mkv.name, main_dest.name)
    shutil.move(str(main_mkv), str(main_dest))

    # Organize extras
    extras = mkvs[1:]
    if extras_map is None:
        extras_map = {}

    for extra in extras:
        extra_type = extras_map.get(extra, ExtraType.EXTRAS)
        extra_dir = dest / extra_type.value
        extra_dir.mkdir(exist_ok=True)
        logger.info("  -> %s/%s", extra_type.value, extra.name)
        shutil.move(str(extra), str(extra_dir / extra.name))

    # Clean up empty staging dir
    _remove_if_empty(staging_dir)

    logger.info("Movie organized: %s", dest)
    return dest


def organize_tv(
    staging_dir: Path,
    show_name: str,
    season_num: int,
    episode_map: dict[Path, int],
    settings: Settings,
) -> Path:
    """Organize ripped files into Emby TV folder structure.

    Args:
        staging_dir: Directory containing ripped MKV files.
        show_name: TV show name, e.g. "Seinfeld".
        season_num: Season number.
        episode_map: Mapping of file paths to episode numbers.
        settings: Application settings.

    Returns:
        Path to the organized season directory.
    """
    season_dir = settings.tv_dir / show_name / f"Season {season_num:02d}"
    season_dir.mkdir(parents=True, exist_ok=True)

    for mkv_path, ep_num in episode_map.items():
        ep_name = f"{show_name} - S{season_num:02d}E{ep_num:02d}.mkv"
        dest = season_dir / ep_name
        logger.info("  -> %s", ep_name)
        shutil.move(str(mkv_path), str(dest))

    _remove_if_empty(staging_dir)

    logger.info("TV episodes organized: %s", season_dir)
    return season_dir


def organize_multi_disc(
    disc_dirs: list[Path],
    movie_name: str,
    settings: Settings,
    merge: bool = True,
    extras_map: dict[Path, ExtraType] | None = None,
) -> Path:
    """Organize a multi-disc movie rip.

    Identifies the largest file on each disc as the main feature segment,
    optionally merges them, and collects extras from all discs.

    Returns:
        Path to the organized movie directory.
    """
    dest = settings.movies_dir / movie_name
    dest.mkdir(parents=True, exist_ok=True)

    main_segments: list[Path] = []
    all_extras: list[Path] = []

    for disc_dir in disc_dirs:
        mkvs = sorted(
            disc_dir.glob("*.mkv"),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if not mkvs:
            logger.warning("No MKV files in %s, skipping", disc_dir)
            continue
        main_segments.append(mkvs[0])
        all_extras.extend(mkvs[1:])

    if not main_segments:
        raise FileNotFoundError("No main feature segments found across discs")

    # Handle main feature
    if merge and len(main_segments) > 1:
        _merge_segments(main_segments, dest, movie_name)
    elif len(main_segments) > 1:
        # Emby multi-part naming
        for i, seg in enumerate(main_segments, 1):
            part_name = f"{movie_name} - part{i}.mkv"
            shutil.move(str(seg), str(dest / part_name))
    else:
        shutil.move(str(main_segments[0]), str(dest / f"{movie_name}.mkv"))

    # Organize extras from all discs
    if extras_map is None:
        extras_map = {}

    for extra in all_extras:
        extra_type = extras_map.get(extra, ExtraType.EXTRAS)
        extra_dir = dest / extra_type.value
        extra_dir.mkdir(exist_ok=True)
        shutil.move(str(extra), str(extra_dir / extra.name))

    # Clean up disc staging dirs
    for disc_dir in disc_dirs:
        _remove_if_empty(disc_dir)

    logger.info("Multi-disc movie organized: %s", dest)
    return dest


def _merge_segments(segments: list[Path], dest: Path, movie_name: str) -> None:
    """Merge MKV segments using mkvmerge."""
    if not shutil.which("mkvmerge"):
        logger.warning("mkvmerge not found, falling back to multi-part naming")
        for i, seg in enumerate(segments, 1):
            shutil.move(str(seg), str(dest / f"{movie_name} - part{i}.mkv"))
        return

    output = dest / f"{movie_name}.mkv"
    cmd = ["mkvmerge", "-o", str(output), str(segments[0])]
    for seg in segments[1:]:
        cmd.extend(["+", str(seg)])

    logger.info("Merging %d segments with mkvmerge...", len(segments))
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        logger.info("Merge complete: %s", output.name)
        for seg in segments:
            seg.unlink()
    else:
        logger.error("mkvmerge failed: %s", result.stderr)
        logger.warning("Falling back to multi-part naming")
        for i, seg in enumerate(segments, 1):
            shutil.move(str(seg), str(dest / f"{movie_name} - part{i}.mkv"))


def _remove_if_empty(path: Path) -> None:
    """Remove directory if it's empty."""
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        logger.warning("Could not remove directory %s", path, exc_info=True)
