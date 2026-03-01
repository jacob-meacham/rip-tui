"""Tests for rip flow pipeline functions."""

from pathlib import Path
from unittest.mock import patch

import pytest

from ripper.config.settings import Settings
from ripper.core.disc import DiscInfo, Title
from ripper.tui.flows import (
    cleanup_backup,
    create_backup,
    enrich_disc_info,
    remux_from_backup,
)


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings with temp directories for testing."""
    return Settings(
        staging_dir=tmp_path / "staging",
        movies_dir=tmp_path / "movies",
        tv_dir=tmp_path / "tv",
        device="/dev/null",
        tmdb_api_key="",
    )


@pytest.fixture
def disc_info() -> DiscInfo:
    return DiscInfo(
        name="TEST_DISC",
        device="/dev/null",
        titles=[
            Title(
                id=0,
                name="Main Feature",
                duration_seconds=7200,
                size_bytes=30_000_000_000,
                chapter_count=20,
                is_main_feature=True,
            ),
        ],
    )


class TestCreateBackup:
    def test_calls_backup_disc_with_correct_path(
        self, settings, tmp_path
    ):
        staging = tmp_path / "staging"

        with patch("ripper.tui.flows.backup_disc"), \
             patch("ripper.tui.flows.start_rip_with_status") as mock_start:
            # Make start_rip_with_status call the function
            mock_start.side_effect = lambda label, fn, *a, **kw: None

            result = create_backup(settings, staging)

        assert result == staging / ".backup"

    def test_cleans_partial_backup(self, settings, tmp_path):
        staging = tmp_path / "staging"
        old_backup = staging / ".backup"
        old_backup.mkdir(parents=True)
        (old_backup / "old_file.txt").write_text("leftover")
        # No BDMV/STREAM — not a valid backup, should be cleaned

        with patch("ripper.tui.flows.start_rip_with_status"):
            result = create_backup(settings, staging)

        assert result == staging / ".backup"

    def test_cleans_existing_backup_before_fresh(
        self, settings, tmp_path
    ):
        staging = tmp_path / "staging"
        backup = staging / ".backup"
        stream = backup / "BDMV" / "STREAM"
        stream.mkdir(parents=True)
        (stream / "00000.m2ts").write_bytes(b"\x00" * 100)

        with patch("ripper.tui.flows.start_rip_with_status"):
            result = create_backup(settings, staging)

        assert result == backup
        # Old M2TS should be gone (cleaned up for fresh backup)
        assert not (stream / "00000.m2ts").exists()


class TestEnrichDiscInfo:
    def test_computes_hash_and_calls_discdb_when_enabled(
        self, disc_info, settings
    ):
        settings.discdb_enabled = True
        backup_dir = Path("/fake/backup")
        discdb_result = {
            "title": "Test Movie",
            "year": 2021,
            "type": "Movie",
            "titles": [],
        }

        with patch(
            "ripper.tui.flows.compute_hash_from_backup",
            return_value="ABC123",
        ), patch(
            "ripper.tui.flows._sync_discdb_lookup",
            return_value=discdb_result,
        ):
            enrich_disc_info(disc_info, backup_dir, settings)

        assert disc_info.content_hash == "ABC123"
        assert disc_info.discdb_title == "Test Movie"
        assert disc_info.discdb_year == 2021

    def test_skips_discdb_when_disabled(self, disc_info, settings):
        settings.discdb_enabled = False
        backup_dir = Path("/fake/backup")

        with patch(
            "ripper.tui.flows.compute_hash_from_backup",
            return_value="ABC123",
        ), patch(
            "ripper.tui.flows._sync_discdb_lookup",
        ) as mock_discdb:
            enrich_disc_info(disc_info, backup_dir, settings)

        assert disc_info.content_hash == "ABC123"
        mock_discdb.assert_not_called()

    def test_handles_no_hash(self, disc_info, settings):
        backup_dir = Path("/fake/backup")

        with patch(
            "ripper.tui.flows.compute_hash_from_backup",
            return_value=None,
        ), patch(
            "ripper.tui.flows._sync_discdb_lookup",
        ) as mock_discdb:
            enrich_disc_info(disc_info, backup_dir, settings)

        mock_discdb.assert_not_called()


class TestRemuxFromBackup:
    def test_remuxes_all_when_no_titles(self, settings):
        backup_dir = Path("/fake/backup")
        staging = Path("/fake/staging")

        with patch(
            "ripper.tui.flows.start_rip_with_status"
        ) as mock_start:
            remux_from_backup(
                backup_dir, staging, "Test", settings
            )

        mock_start.assert_called_once()
        call_args = mock_start.call_args
        assert call_args[0][0] == "Remuxing: Test"
        # Second positional arg is remux_all_from_backup
        from ripper.core.ripper import remux_all_from_backup
        assert call_args[0][1] is remux_all_from_backup

    def test_remuxes_specific_titles(self, settings, disc_info):
        backup_dir = Path("/fake/backup")
        staging = Path("/fake/staging")

        with patch(
            "ripper.tui.flows.start_rip_with_status"
        ) as mock_start:
            remux_from_backup(
                backup_dir, staging, "Test", settings,
                titles=disc_info.titles,
            )

        mock_start.assert_called_once()
        call_args = mock_start.call_args
        from ripper.core.ripper import remux_titles_from_backup
        assert call_args[0][1] is remux_titles_from_backup


class TestCleanupBackup:
    def test_removes_backup_dir(self, tmp_path):
        staging = tmp_path / "staging"
        backup = staging / ".backup"
        backup.mkdir(parents=True)
        (backup / "data.bin").write_text("content")

        cleanup_backup(staging)

        assert not backup.exists()

    def test_handles_missing_dir_gracefully(self, tmp_path):
        staging = tmp_path / "staging"
        # No .backup directory exists — should not raise
        cleanup_backup(staging)
