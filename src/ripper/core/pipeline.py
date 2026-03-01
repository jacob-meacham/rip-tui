"""Shared setup pipeline for CLI and TUI rip flows."""

import logging
from pathlib import Path

from ripper.config.settings import Settings
from ripper.core.disc import DiscInfo
from ripper.core.scanner import scan_disc
from ripper.metadata.classifier import classify_titles
from ripper.tui.flows import (
    backup_is_valid,
    create_backup,
    enrich_disc_info,
)

logger = logging.getLogger(__name__)


def setup_rip(
    settings: Settings,
    backup: Path | None = None,
) -> tuple[DiscInfo, Path]:
    """Scan, classify, backup, enrich. Used by both CLI and TUI.

    Args:
        settings: Application settings.
        backup: Optional path to existing BDMV backup. When provided,
            skips disc backup and validates the path instead.

    Returns:
        Tuple of (disc_info, backup_dir).

    Raises:
        FileNotFoundError: If backup path is invalid.
    """
    disc_info = scan_disc(settings)
    classify_titles(disc_info.titles, settings.min_main_length)

    if backup is not None:
        if not backup_is_valid(backup):
            raise FileNotFoundError(
                f"Invalid backup: no BDMV/STREAM with M2TS files in {backup}"
            )
        backup_dir = backup
    else:
        # Use a temporary staging area for the backup
        staging_dir = settings.staging_dir / ".pipeline-backup"
        backup_dir = create_backup(settings, staging_dir)

    enrich_disc_info(disc_info, backup_dir, settings)
    return disc_info, backup_dir
