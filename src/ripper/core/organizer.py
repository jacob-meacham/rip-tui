"""File organization for Emby-compatible folder structures."""

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ripper.config.settings import Settings
from ripper.core.disc import ExtraType
from ripper.metadata.classifier import classify_extra

logger = logging.getLogger(__name__)

_MULTI_DISC_DIR_RE = re.compile(
    r"^(?P<name>.+)-disc(?P<disc>\d+)$",
    re.IGNORECASE,
)
_TV_SEASON_DIR_RE = re.compile(
    r"^(?P<show>.+)-s(?P<season>\d{1,2})$",
    re.IGNORECASE,
)


@dataclass
class ReorganizeStagingResult:
    """Result summary for a staging reorganization pass."""

    movies: list[Path] = field(default_factory=list)
    tv: list[Path] = field(default_factory=list)
    multi_disc: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def processed_count(self) -> int:
        return len(self.movies) + len(self.tv) + len(self.multi_disc)


def find_mkv_files(root: Path) -> list[Path]:
    """Return MKV files under root (recursive, case-insensitive), largest first."""
    if not root.exists():
        return []

    mkvs = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() == ".mkv"
    ]
    return sorted(
        mkvs,
        key=lambda p: p.stat().st_size,
        reverse=True,
    )


def reorganize_staging(
    settings: Settings,
    staging_root: Path | None = None,
) -> ReorganizeStagingResult:
    """Re-organize existing rip folders in a staging directory.

    Expected folder patterns under staging root:
    - Movie: `<Movie Name (Year)>`
    - TV season: `<Show Name>-S01`
    - Multi-disc: `<Movie Name>-disc1`, `<Movie Name>-disc2`, ...
    """
    root = staging_root or settings.staging_dir
    result = ReorganizeStagingResult()

    if not root.exists() or not root.is_dir():
        return result

    multi_disc_groups: dict[str, list[tuple[int, Path]]] = {}
    entries = sorted(
        (entry for entry in root.iterdir() if entry.is_dir()),
        key=lambda p: p.name.lower(),
    )
    for entry in entries:
        mkvs = find_mkv_files(entry)
        if not mkvs:
            result.skipped.append(entry)
            _remove_if_empty(entry)
            continue

        multi_match = _MULTI_DISC_DIR_RE.match(entry.name)
        if multi_match:
            movie_name = multi_match.group("name").strip()
            disc_num = int(multi_match.group("disc"))
            multi_disc_groups.setdefault(movie_name, []).append(
                (disc_num, entry)
            )
            continue

        tv_match = _TV_SEASON_DIR_RE.match(entry.name)
        if tv_match:
            show_name = tv_match.group("show").strip()
            season_num = int(tv_match.group("season"))
            episode_map = {mkv: i + 1 for i, mkv in enumerate(mkvs)}
            try:
                season_dir = organize_tv(
                    entry,
                    show_name,
                    season_num,
                    episode_map,
                    settings,
                )
                result.tv.append(season_dir)
            except Exception as exc:
                result.errors.append((entry, str(exc)))
            continue

        try:
            movie_dir = organize_movie(entry, entry.name, settings)
            result.movies.append(movie_dir)
        except Exception as exc:
            result.errors.append((entry, str(exc)))

    for movie_name, grouped in sorted(multi_disc_groups.items()):
        disc_dirs = [
            path for _, path in sorted(grouped, key=lambda item: item[0])
        ]
        try:
            movie_dir = organize_multi_disc(disc_dirs, movie_name, settings)
            result.multi_disc.append(movie_dir)
        except Exception as exc:
            error_text = str(exc)
            for _, disc_dir in grouped:
                result.errors.append((disc_dir, error_text))

    _remove_if_empty(root)
    return result


def organize_movie(
    staging_dir: Path,
    movie_name: str,
    settings: Settings,
    extras_map: dict[Path, ExtraType] | None = None,
    main_mkv: Path | None = None,
    names_map: dict[Path, str] | None = None,
) -> Path:
    """Organize ripped files into Emby movie folder structure.

    Args:
        staging_dir: Directory containing ripped MKV files.
        movie_name: Movie name with year, e.g. "Dune (2021)".
        settings: Application settings.
        extras_map: Optional mapping of file paths to extra types.
            Files not in this map and not the main feature go to 'extras/'.
        main_mkv: Explicit main feature file. Falls back to largest.
        names_map: Optional mapping of file paths to display names
            (e.g. from DiscDB). Used to rename extras.

    Returns:
        Path to the organized movie directory.
    """
    dest = settings.movies_dir / movie_name
    dest.mkdir(parents=True, exist_ok=True)

    mkvs = find_mkv_files(staging_dir)
    if not mkvs:
        raise FileNotFoundError(f"No MKV files found in {staging_dir}")

    # Use specified main feature or fall back to largest
    if main_mkv is None:
        main_mkv = mkvs[0]
    main_dest = dest / f"{movie_name}.mkv"
    logger.info("Main feature: %s -> %s", main_mkv.name, main_dest.name)
    shutil.move(str(main_mkv), str(main_dest))

    # Organize extras
    extras = [m for m in mkvs if m != main_mkv]
    if extras_map is None:
        extras_map = {}
    if names_map is None:
        names_map = {}

    for extra in extras:
        extra_type = extras_map.get(extra) or classify_extra(extra.stem)
        extra_dir = dest / extra_type.value
        extra_dir.mkdir(exist_ok=True)
        # Use DiscDB name if available, otherwise keep original
        display_name = names_map.get(extra)
        dest_name = f"{display_name}.mkv" if display_name else extra.name
        logger.info("  -> %s/%s", extra_type.value, dest_name)
        shutil.move(str(extra), str(extra_dir / dest_name))

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
        mkvs = find_mkv_files(disc_dir)
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
        extra_type = extras_map.get(extra) or classify_extra(extra.stem)
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
    """Remove path and any empty subdirectories beneath it."""
    try:
        if not path.is_dir():
            return

        # Remove nested empty directories first, then the root.
        for subdir in sorted(
            (p for p in path.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            if not any(subdir.iterdir()):
                subdir.rmdir()

        if not any(path.iterdir()):
            path.rmdir()
    except OSError:
        logger.warning("Could not remove directory %s", path, exc_info=True)
