"""Typer CLI interface for ripper."""

import logging
from pathlib import Path

import typer

from ripper.config.settings import Settings

app = typer.Typer(
    name="rip",
    help="4K Blu-ray Ripper for Emby media servers.",
    no_args_is_help=False,
)

logger = logging.getLogger(__name__)


def _get_settings() -> Settings:
    """Load settings, warning on config errors."""
    try:
        return Settings()
    except Exception as e:
        logger.warning("Failed to load config: %s", e)
        logger.warning("Using default settings")
        return Settings.model_construct()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Launch interactive TUI if no subcommand given."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if ctx.invoked_subcommand is not None:
        return

    from ripper.tui.app import RipperApp

    settings = _get_settings()
    rip_app = RipperApp(settings)
    rip_app.run()


@app.command()
def movie(
    name: str = typer.Argument(..., help="Movie name with year, e.g. 'Dune (2021)'"),
    no_extras: bool = typer.Option(
        False, "--no-extras", help="Skip extras, rip main feature only"
    ),
) -> None:
    """Rip a single-disc movie."""
    settings = _get_settings()
    staging = settings.staging_dir / name

    from ripper.core.organizer import organize_movie
    from ripper.core.ripper import rip_all_titles
    from ripper.core.scanner import scan_disc
    from ripper.utils.drive import eject_disc

    def _log_progress(p):
        typer.echo(f"\r  {p.title_name}: {p.percent:.1f}%", nl=False)

    typer.echo(f"Ripping: {name}")

    if no_extras:
        disc = scan_disc(settings)
        from ripper.core.ripper import rip_titles

        main_titles = [t for t in disc.titles if t.is_main_feature]
        if not main_titles:
            # Fallback: use longest title
            main_titles = sorted(
                disc.titles,
                key=lambda t: t.duration_seconds,
                reverse=True,
            )[:1]
        rip_titles(main_titles, staging, settings, on_progress=_log_progress)
    else:
        rip_all_titles(staging, settings, on_progress=_log_progress)

    typer.echo("")
    organize_movie(staging, name, settings)

    if settings.auto_eject:
        eject_disc(settings.device)
    typer.echo(f"Done: {settings.movies_dir / name}")


@app.command()
def multi(
    name: str = typer.Argument(..., help="Movie name with year"),
    discs: int = typer.Option(2, "--discs", "-d", help="Number of discs"),
    no_merge: bool = typer.Option(
        False, "--no-merge", help="Keep parts separate instead of merging"
    ),
) -> None:
    """Rip a multi-disc movie."""
    settings = _get_settings()

    from ripper.core.organizer import organize_multi_disc
    from ripper.core.ripper import rip_all_titles
    from ripper.utils.drive import eject_disc, wait_for_disc

    def _log_progress(p):
        typer.echo(f"\r  {p.title_name}: {p.percent:.1f}%", nl=False)

    disc_dirs: list[Path] = []

    for d in range(1, discs + 1):
        if d > 1:
            eject_disc(settings.device)
            typer.echo(f"\nInsert disc {d} and press Enter...")
            input()
            typer.echo("Waiting for disc...")
            if not wait_for_disc(settings.device):
                typer.echo(f"Timed out waiting for disc {d}", err=True)
                raise typer.Exit(1)

        disc_staging = settings.staging_dir / f"{name}-disc{d}"
        typer.echo(f"Ripping disc {d}/{discs}...")
        rip_all_titles(disc_staging, settings, on_progress=_log_progress)
        disc_dirs.append(disc_staging)

    typer.echo("")
    organize_multi_disc(disc_dirs, name, settings, merge=not no_merge)

    if settings.auto_eject:
        eject_disc(settings.device)
    typer.echo(f"Done: {settings.movies_dir / name}")


@app.command()
def tv(
    show: str = typer.Argument(..., help="TV show name, e.g. 'Seinfeld'"),
    season: int = typer.Argument(..., help="Season number"),
) -> None:
    """Rip a TV disc and organize episodes."""
    settings = _get_settings()
    staging = settings.staging_dir / f"{show}-S{season:02d}"

    from ripper.core.organizer import organize_tv
    from ripper.core.ripper import rip_all_titles
    from ripper.utils.drive import eject_disc

    def _log_progress(p):
        typer.echo(f"\r  {p.title_name}: {p.percent:.1f}%", nl=False)

    typer.echo(f"Ripping: {show} Season {season}")
    rip_all_titles(staging, settings, on_progress=_log_progress)
    typer.echo("")

    # Auto-map by size (largest first)
    mkvs = sorted(staging.glob("*.mkv"), key=lambda p: p.stat().st_size, reverse=True)
    episode_map = {mkv: i + 1 for i, mkv in enumerate(mkvs)}

    season_dir = organize_tv(staging, show, season, episode_map, settings)
    if settings.auto_eject:
        eject_disc(settings.device)
    typer.echo(f"Done: {season_dir}")


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
def eject() -> None:
    """Eject the disc."""
    settings = _get_settings()
    from ripper.utils.drive import eject_disc

    if eject_disc(settings.device):
        typer.echo("Disc ejected")
    else:
        typer.echo("Eject failed", err=True)
        raise typer.Exit(1)
