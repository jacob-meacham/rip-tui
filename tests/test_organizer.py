"""Tests for file organization into Emby structure."""

from pathlib import Path

from ripper.config.settings import Settings
from ripper.core.disc import ExtraType
from ripper.core.organizer import organize_movie, organize_tv


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        staging_dir=tmp_path / "staging",
        movies_dir=tmp_path / "movies",
        tv_dir=tmp_path / "tv",
        device="/dev/null",
        tmdb_api_key="",
    )


def _create_mkv(path: Path, size: int) -> Path:
    """Create a fake MKV file with given size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    return path


class TestOrganizeMovie:
    def test_main_feature_placed_correctly(self, tmp_path):
        settings = _make_settings(tmp_path)
        staging = settings.staging_dir / "Test Movie (2024)"
        _create_mkv(staging / "title00.mkv", 10000)

        result = organize_movie(staging, "Test Movie (2024)", settings)

        expected = settings.movies_dir / "Test Movie (2024)" / "Test Movie (2024).mkv"
        assert expected.exists()
        assert result == settings.movies_dir / "Test Movie (2024)"

    def test_largest_file_is_main_feature(self, tmp_path):
        settings = _make_settings(tmp_path)
        staging = settings.staging_dir / "Test Movie (2024)"
        _create_mkv(staging / "title00.mkv", 5000)   # smaller
        _create_mkv(staging / "title01.mkv", 10000)  # largest = main
        _create_mkv(staging / "title02.mkv", 3000)   # smallest

        organize_movie(staging, "Test Movie (2024)", settings)

        main = settings.movies_dir / "Test Movie (2024)" / "Test Movie (2024).mkv"
        assert main.exists()
        assert main.stat().st_size == 10000

    def test_extras_go_to_extras_folder(self, tmp_path):
        settings = _make_settings(tmp_path)
        staging = settings.staging_dir / "Test Movie (2024)"
        _create_mkv(staging / "title00.mkv", 10000)  # main
        _create_mkv(staging / "title01.mkv", 3000)   # extra

        organize_movie(staging, "Test Movie (2024)", settings)

        extras_dir = settings.movies_dir / "Test Movie (2024)" / "extras"
        assert extras_dir.exists()
        assert (extras_dir / "title01.mkv").exists()

    def test_extras_classified_by_map(self, tmp_path):
        settings = _make_settings(tmp_path)
        staging = settings.staging_dir / "Test Movie (2024)"
        _create_mkv(staging / "title00.mkv", 10000)
        bts = _create_mkv(staging / "title01.mkv", 3000)
        trailer = _create_mkv(staging / "title02.mkv", 1000)

        extras_map = {
            bts: ExtraType.BEHIND_THE_SCENES,
            trailer: ExtraType.TRAILERS,
        }
        organize_movie(staging, "Test Movie (2024)", settings, extras_map=extras_map)

        movie_dir = settings.movies_dir / "Test Movie (2024)"
        assert (movie_dir / "behind the scenes" / "title01.mkv").exists()
        assert (movie_dir / "trailers" / "title02.mkv").exists()

    def test_no_extras_just_main(self, tmp_path):
        settings = _make_settings(tmp_path)
        staging = settings.staging_dir / "Test Movie (2024)"
        _create_mkv(staging / "title00.mkv", 10000)

        organize_movie(staging, "Test Movie (2024)", settings)

        movie_dir = settings.movies_dir / "Test Movie (2024)"
        assert (movie_dir / "Test Movie (2024).mkv").exists()
        # No extras subfolder should exist
        assert not (movie_dir / "extras").exists()

    def test_staging_dir_cleaned_up(self, tmp_path):
        settings = _make_settings(tmp_path)
        staging = settings.staging_dir / "Test Movie (2024)"
        _create_mkv(staging / "title00.mkv", 10000)

        organize_movie(staging, "Test Movie (2024)", settings)

        # Staging dir should be removed if empty
        assert not staging.exists()


class TestOrganizeTV:
    def test_episodes_named_correctly(self, tmp_path):
        settings = _make_settings(tmp_path)
        staging = settings.staging_dir / "Show-S01"
        ep1 = _create_mkv(staging / "title00.mkv", 5000)
        ep2 = _create_mkv(staging / "title01.mkv", 4000)

        episode_map = {ep1: 1, ep2: 2}
        result = organize_tv(staging, "Breaking Bad", 1, episode_map, settings)

        expected_dir = settings.tv_dir / "Breaking Bad" / "Season 01"
        assert result == expected_dir
        assert (expected_dir / "Breaking Bad - S01E01.mkv").exists()
        assert (expected_dir / "Breaking Bad - S01E02.mkv").exists()

    def test_season_directory_created(self, tmp_path):
        settings = _make_settings(tmp_path)
        staging = settings.staging_dir / "Show-S03"
        ep = _create_mkv(staging / "title00.mkv", 5000)

        episode_map = {ep: 5}
        result = organize_tv(staging, "Seinfeld", 3, episode_map, settings)

        assert result == settings.tv_dir / "Seinfeld" / "Season 03"
        assert (result / "Seinfeld - S03E05.mkv").exists()

    def test_staging_cleaned_up(self, tmp_path):
        settings = _make_settings(tmp_path)
        staging = settings.staging_dir / "Show-S01"
        ep = _create_mkv(staging / "title00.mkv", 5000)

        episode_map = {ep: 1}
        organize_tv(staging, "Show", 1, episode_map, settings)

        assert not staging.exists()
