"""Tests for batch-mode backup normalization.

Ensures create_backup always returns the .backup subdir regardless of
whether a custom on_progress / process_id is supplied.
"""

from unittest.mock import MagicMock, patch

import pytest

from ripper.config.settings import Settings
from ripper.tui.flows import create_backup


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


class TestCreateBackupBatch:
    """Verify create_backup returns .backup subdir in all call variants."""

    def test_default_progress_returns_backup_subdir(
        self, settings, tmp_path,
    ):
        """Default call (no on_progress) returns staging/.backup."""
        staging = tmp_path / "my-staging"

        with patch("ripper.tui.flows.start_rip_with_status"):
            result = create_backup(settings, staging)

        assert result == staging / ".backup"
        assert result.name == ".backup"

    def test_custom_progress_returns_backup_subdir(
        self, settings, tmp_path,
    ):
        """Concurrent call with custom on_progress returns staging/.backup."""
        staging = tmp_path / "my-staging"
        custom_cb = MagicMock()

        with patch("ripper.tui.flows.start_rip_with_status"):
            result = create_backup(
                settings, staging,
                on_progress=custom_cb,
                process_id="backup-disc2",
            )

        assert result == staging / ".backup"
        assert result.name == ".backup"

    def test_custom_progress_forwarded_to_rip(
        self, settings, tmp_path,
    ):
        """Custom on_progress and process_id are forwarded to the rip fn."""
        staging = tmp_path / "my-staging"
        custom_cb = MagicMock()

        with patch(
            "ripper.tui.flows.start_rip_with_status",
        ) as mock_start:
            create_backup(
                settings, staging,
                on_progress=custom_cb,
                process_id="backup-disc3",
            )

        mock_start.assert_called_once()
        _, kwargs = mock_start.call_args
        assert kwargs["on_progress"] is custom_cb
        assert kwargs["process_id"] == "backup-disc3"

    def test_default_uses_print_progress(self, settings, tmp_path):
        """When no on_progress given, print_progress is used."""
        from ripper.tui.display import print_progress

        staging = tmp_path / "my-staging"

        with patch(
            "ripper.tui.flows.start_rip_with_status",
        ) as mock_start:
            create_backup(settings, staging)

        _, kwargs = mock_start.call_args
        assert kwargs["on_progress"] is print_progress

    def test_creates_staging_parent_dir(self, settings, tmp_path):
        """Staging dir is created if it doesn't exist."""
        staging = tmp_path / "deep" / "nested" / "staging"
        assert not staging.exists()

        with patch("ripper.tui.flows.start_rip_with_status"):
            result = create_backup(settings, staging)

        assert staging.exists()
        assert result == staging / ".backup"
