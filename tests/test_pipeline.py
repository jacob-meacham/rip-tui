"""Tests for the shared CLI/TUI setup pipeline."""

from pathlib import Path
from unittest.mock import patch

import pytest

from ripper.core.disc import DiscInfo, Title
from ripper.core.pipeline import setup_rip


@pytest.fixture
def disc_info():
    return DiscInfo(
        name="TEST_DISC",
        device="/dev/sr0",
        titles=[
            Title(
                id=0,
                name="Main Feature",
                duration_seconds=7200,
                size_bytes=30_000_000_000,
                chapter_count=24,
                is_main_feature=True,
            ),
        ],
    )


class TestSetupRip:
    def test_scans_classifies_and_enriches(self, settings, disc_info):
        with (
            patch(
                "ripper.core.pipeline.scan_disc",
                return_value=disc_info,
            ) as mock_scan,
            patch(
                "ripper.core.pipeline.classify_titles",
            ) as mock_classify,
            patch(
                "ripper.core.pipeline.create_backup",
                return_value=Path("/fake/backup"),
            ) as mock_backup,
            patch(
                "ripper.core.pipeline.enrich_disc_info",
            ) as mock_enrich,
        ):
            result_info, result_dir = setup_rip(settings)

        mock_scan.assert_called_once_with(settings)
        mock_classify.assert_called_once_with(
            disc_info.titles, settings.min_main_length,
        )
        mock_backup.assert_called_once()
        mock_enrich.assert_called_once_with(
            disc_info, Path("/fake/backup"), settings,
        )
        assert result_info is disc_info
        assert result_dir == Path("/fake/backup")

    def test_uses_external_backup_when_provided(
        self, settings, disc_info, tmp_path,
    ):
        # Create a valid backup structure
        backup = tmp_path / "my_backup"
        stream = backup / "BDMV" / "STREAM"
        stream.mkdir(parents=True)
        (stream / "00001.m2ts").write_bytes(b"\x00" * 100)

        with (
            patch(
                "ripper.core.pipeline.scan_disc",
                return_value=disc_info,
            ),
            patch("ripper.core.pipeline.classify_titles"),
            patch("ripper.core.pipeline.create_backup") as mock_backup,
            patch("ripper.core.pipeline.enrich_disc_info"),
        ):
            result_info, result_dir = setup_rip(
                settings, backup=backup,
            )

        mock_backup.assert_not_called()
        assert result_dir == backup

    def test_invalid_backup_raises_error(self, settings, disc_info, tmp_path):
        # Create an invalid backup (no BDMV/STREAM)
        bad_backup = tmp_path / "bad_backup"
        bad_backup.mkdir()

        with (
            patch(
                "ripper.core.pipeline.scan_disc",
                return_value=disc_info,
            ),
            patch("ripper.core.pipeline.classify_titles"),
            pytest.raises(FileNotFoundError, match="Invalid backup"),
        ):
            setup_rip(settings, backup=bad_backup)
