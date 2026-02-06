"""Shared test fixtures."""

import pytest

from ripper.config.settings import Settings


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
