"""Typer CLI interface for ripper."""

import logging
from pathlib import Path
from typing import cast

import typer

from ripper.config.settings import Settings
from ripper.core.ripper import RipCancelledError

app = typer.Typer(
    name="rip",
    help="4K Blu-ray Ripper for Emby media servers.",
    no_args_is_help=False,
)

logger = logging.getLogger(__name__)


def _log_progress(p) -> None:
    """Print single-line progress update for CLI commands."""
    typer.echo(f"\r  {p.title_name}: {p.percent:.1f}%", nl=False)


def _get_settings() -> Settings:
    """Load settings, warning on config errors."""
    try:
        return Settings()
    except Exception as e:
        logger.warning("Failed to load config: %s", e)
        logger.warning("Using default settings")
        return Settings.model_construct()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    backup: Path | None = typer.Option(
        None,
        "--backup",
        "-b",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Path to existing BDMV backup (skips backup step).",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show debug output.",
    ),
) -> None:
    """Launch interactive TUI if no subcommand given."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if ctx.invoked_subcommand is not None:
        return

    from ripper.tui.app import run_interactive

    settings = _get_settings()
    run_interactive(settings, external_backup=backup, verbose=verbose)


@app.command()
def movie(
    name: str = typer.Argument(
        ..., help="Movie name with year, e.g. 'Dune (2021)'"
    ),
    no_extras: bool = typer.Option(
        False, "--no-extras", help="Skip extras, rip main feature only"
    ),
    backup: Path | None = typer.Option(
        None,
        "--backup",
        "-b",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Path to existing BDMV backup (skips backup step).",
    ),
) -> None:
    """Rip a single-disc movie."""
    settings = _get_settings()
    staging = settings.staging_dir / name

    from ripper.core.organizer import organize_movie
    from ripper.core.ripper import (
        backup_disc,
        remux_all_from_backup,
        remux_titles_from_backup,
    )
    from ripper.core.scanner import compute_hash_from_backup, scan_disc
    from ripper.metadata.classifier import classify_titles
    from ripper.tui.flows import cleanup_backup
    from ripper.utils.drive import eject_disc

    external = backup is not None

    try:
        typer.echo("Scanning disc...")
        disc_info = scan_disc(settings)
        classify_titles(disc_info.titles, settings.min_main_length)

        if external:
            backup_dir = backup
        else:
            typer.echo("Backing up disc...")
            backup_dir = staging / ".backup"
            backup_disc(
                backup_dir, settings, on_progress=_log_progress
            )
            typer.echo("")

        content_hash = compute_hash_from_backup(backup_dir)
        if content_hash:
            disc_info.content_hash = content_hash

        typer.echo(f"Remuxing: {name}")
        if no_extras:
            main_titles = [
                t for t in disc_info.titles if t.is_main_feature
            ]
            if not main_titles:
                main_titles = sorted(
                    disc_info.titles,
                    key=lambda t: t.duration_seconds,
                    reverse=True,
                )[:1]
            remux_titles_from_backup(
                backup_dir, main_titles, staging, settings,
                on_progress=_log_progress,
            )
        else:
            remux_all_from_backup(
                backup_dir, staging, settings,
                on_progress=_log_progress,
            )

        typer.echo("")
        organize_movie(staging, name, settings)
        if not external:
            cleanup_backup(staging)

        if settings.auto_eject:
            eject_disc(settings.device)
        typer.echo(f"Done: {settings.movies_dir / name}")
    except RipCancelledError:
        typer.echo("\nCancelled by user.", err=True)
        raise typer.Exit(1)
    except Exception as e:
        logger.error("Rip failed: %s", e, exc_info=True)
        typer.echo(f"\nError: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def multi(
    name: str = typer.Argument(..., help="Movie name with year"),
    discs: int = typer.Option(
        2, "--discs", "-d", help="Number of discs"
    ),
    no_merge: bool = typer.Option(
        False,
        "--no-merge",
        help="Keep parts separate instead of merging",
    ),
    backup: Path | None = typer.Option(
        None,
        "--backup",
        "-b",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help=(
            "Path to existing BDMV backup for disc 1"
            " (skips backup step)."
        ),
    ),
) -> None:
    """Rip a multi-disc movie."""
    settings = _get_settings()

    from ripper.core.organizer import organize_multi_disc
    from ripper.core.ripper import backup_disc, remux_all_from_backup
    from ripper.core.scanner import compute_hash_from_backup, scan_disc
    from ripper.metadata.classifier import classify_titles
    from ripper.tui.flows import cleanup_backup
    from ripper.utils.drive import eject_disc, wait_for_disc

    try:
        disc_dirs: list[Path] = []

        for d in range(1, discs + 1):
            if d > 1:
                eject_disc(settings.device)
                typer.echo(f"\nInsert disc {d} and press Enter...")
                input()
                typer.echo("Waiting for disc...")
                if not wait_for_disc(settings.device):
                    typer.echo(
                        f"Timed out waiting for disc {d}",
                        err=True,
                    )
                    raise typer.Exit(1)

            disc_staging = settings.staging_dir / f"{name}-disc{d}"

            # Use external backup for disc 1 if provided
            external = d == 1 and backup is not None
            if external:
                backup_dir = backup  # type: ignore[assignment]
            else:
                backup_dir = disc_staging / ".backup"

            typer.echo(f"Scanning disc {d}...")
            disc_info = scan_disc(settings)
            classify_titles(
                disc_info.titles, settings.min_main_length
            )

            if not external:
                typer.echo(f"Backing up disc {d}/{discs}...")
                backup_disc(
                    backup_dir, settings,
                    on_progress=_log_progress,
                )
                typer.echo("")

            content_hash = compute_hash_from_backup(backup_dir)
            if content_hash:
                disc_info.content_hash = content_hash

            typer.echo(f"Remuxing disc {d}/{discs}...")
            remux_all_from_backup(
                backup_dir, disc_staging, settings,
                on_progress=_log_progress,
            )
            typer.echo("")
            if not external:
                cleanup_backup(disc_staging)
            disc_dirs.append(disc_staging)

        organize_multi_disc(disc_dirs, name, settings, merge=not no_merge)

        if settings.auto_eject:
            eject_disc(settings.device)
        typer.echo(f"Done: {settings.movies_dir / name}")
    except RipCancelledError:
        typer.echo("\nCancelled by user.", err=True)
        raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as e:
        logger.error("Rip failed: %s", e, exc_info=True)
        typer.echo(f"\nError: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def tv(
    show: str = typer.Argument(
        ..., help="TV show name, e.g. 'Seinfeld'"
    ),
    season: int = typer.Argument(..., help="Season number"),
    backup: Path | None = typer.Option(
        None,
        "--backup",
        "-b",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Path to existing BDMV backup (skips backup step).",
    ),
) -> None:
    """Rip a TV disc and organize episodes."""
    settings = _get_settings()
    staging = settings.staging_dir / f"{show}-S{season:02d}"

    from ripper.core.organizer import find_mkv_files, organize_tv
    from ripper.core.ripper import backup_disc, remux_all_from_backup
    from ripper.core.scanner import compute_hash_from_backup, scan_disc
    from ripper.metadata.classifier import classify_titles
    from ripper.tui.flows import cleanup_backup
    from ripper.utils.drive import eject_disc

    external = backup is not None

    try:
        typer.echo("Scanning disc...")
        disc_info = scan_disc(settings)
        classify_titles(disc_info.titles, settings.min_main_length)

        if external:
            backup_dir = backup
        else:
            typer.echo("Backing up disc...")
            backup_dir = staging / ".backup"
            backup_disc(
                backup_dir, settings, on_progress=_log_progress
            )
            typer.echo("")

        content_hash = compute_hash_from_backup(backup_dir)
        if content_hash:
            disc_info.content_hash = content_hash

        typer.echo(f"Remuxing: {show} Season {season}")
        remux_all_from_backup(
            backup_dir, staging, settings,
            on_progress=_log_progress,
        )
        typer.echo("")

        # Auto-map by size (largest first)
        mkvs = find_mkv_files(staging)
        episode_map = {mkv: i + 1 for i, mkv in enumerate(mkvs)}

        season_dir = organize_tv(
            staging, show, season, episode_map, settings
        )
        if not external:
            cleanup_backup(staging)

        if settings.auto_eject:
            eject_disc(settings.device)
        typer.echo(f"Done: {season_dir}")
    except RipCancelledError:
        typer.echo("\nCancelled by user.", err=True)
        raise typer.Exit(1)
    except Exception as e:
        logger.error("Rip failed: %s", e, exc_info=True)
        typer.echo(f"\nError: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def info() -> None:
    """Show disc title info without ripping."""
    settings = _get_settings()

    from ripper.core.scanner import scan_disc
    from ripper.metadata.classifier import classify_titles

    disc = scan_disc(settings)
    classify_titles(disc.titles, settings.min_main_length)

    typer.echo(f"Disc: {disc.name}")
    typer.echo(f"{'':>4s} {'Title':<40s} {'Duration':<12s} {'Size':<10s} {'Ch':>4s}")
    typer.echo("-" * 75)
    for t in disc.titles:
        marker = "*" if t.is_main_feature else " "
        typer.echo(
            f" {marker} {t.id:2d} {t.name:<40s} {t.duration_display:<12s} "
            f"{t.size_display:<10s} {t.chapter_count:4d}"
        )


@app.command()
def organize(
    staging: Path | None = typer.Option(
        None,
        "--staging",
        "-s",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Staging root to process (defaults to configured staging_dir).",
    ),
) -> None:
    """Re-organize existing rip folders from staging into libraries."""
    settings = _get_settings()
    root = staging or settings.staging_dir

    from ripper.core.organizer import reorganize_staging

    result = reorganize_staging(settings, staging_root=root)

    typer.echo(f"Staging root: {root}")
    typer.echo(
        "Processed: "
        f"movies={len(result.movies)}, "
        f"tv={len(result.tv)}, "
        f"multi-disc={len(result.multi_disc)}"
    )
    if result.skipped:
        typer.echo(f"Skipped: {len(result.skipped)} directory(s)")
    if result.errors:
        typer.echo(f"Errors: {len(result.errors)}", err=True)
        for source, error in result.errors:
            typer.echo(f"  {source}: {error}", err=True)
        raise typer.Exit(1)
    if result.processed_count == 0:
        typer.echo("Nothing to organize.")
    else:
        typer.echo("Organize pass complete.")


@app.command("debug-progress")
def debug_progress(
    trace: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        file_okay=True,
        resolve_path=True,
        help=(
            "Path to a JSONL trace generated with "
            "RIPPER_PROGRESS_DEBUG=1"
        ),
    ),
    tail: int = typer.Option(
        10,
        "--tail",
        min=1,
        max=100,
        help="Number of tail lines to include in diagnostics.",
    ),
    show_raw: bool = typer.Option(
        False,
        "--show-raw",
        help="Show tail of raw makemkv lines as captured.",
    ),
) -> None:
    """Summarize a progress debug trace."""
    from ripper.core.ripper import summarize_progress_trace

    summary = summarize_progress_trace(trace, tail_size=tail)
    parsed_counts = cast(dict[str, int], summary["parsed_counts"])
    emitted_counts = cast(dict[str, int], summary["emitted_counts"])
    final_progress = cast(
        dict[str, object] | None, summary["final_progress"]
    )
    unparsed_lines = cast(
        list[str], summary["unparsed_progress_lines"]
    )
    raw_tail = cast(list[str], summary["raw_tail"])
    process_exit_code = cast(
        int | None, summary["process_exit_code"]
    )

    typer.echo(f"Trace: {trace}")
    typer.echo(
        "Events: "
        f"{summary['total_events']} "
        f"(malformed: {summary['malformed_lines']})"
    )
    typer.echo(f"Raw lines: {summary['raw_lines']}")
    typer.echo(
        "Parsed lines: "
        + (
            ", ".join(
                f"{kind}={count}"
                for kind, count in sorted(parsed_counts.items())
            )
            if parsed_counts
            else "none"
        )
    )
    typer.echo(
        "Progress emits: "
        + (
            ", ".join(
                f"{kind}={count}"
                for kind, count in sorted(emitted_counts.items())
            )
            if emitted_counts
            else "none"
        )
    )
    typer.echo(
        "Process exit code: "
        + (
            str(process_exit_code)
            if process_exit_code is not None
            else "unknown"
        )
    )

    if final_progress:
        title_name = str(final_progress.get("title_name", ""))
        percent = float(str(final_progress.get("percent", 0.0)))
        current = int(str(final_progress.get("current_bytes", 0)))
        total = int(str(final_progress.get("total_bytes", 0)))
        typer.echo(
            "Final progress: "
            f"{percent:.1f}% "
            f"({current}/{total} bytes) "
            f"title='{title_name}'"
        )
    else:
        typer.echo("Final progress: none emitted")

    if unparsed_lines:
        typer.echo("")
        typer.echo(f"Unparsed PR* lines (tail {len(unparsed_lines)}):")
        for line in unparsed_lines:
            typer.echo(f"  {line}")

    if show_raw and raw_tail:
        typer.echo("")
        typer.echo(f"Raw line tail ({len(raw_tail)}):")
        for line in raw_tail:
            typer.echo(f"  {line}")


@app.command()
def eject() -> None:
    """Eject the disc."""
    settings = _get_settings()
    from ripper.utils.drive import eject_disc

    if eject_disc(settings.device):
        typer.echo("Disc ejected")
    else:
        typer.echo("Eject failed", err=True)
        raise typer.Exit(1)
