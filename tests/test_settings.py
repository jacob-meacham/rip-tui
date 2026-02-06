"""Tests for settings loading from TOML and environment variables."""

from pathlib import Path

from ripper.config.settings import Settings


def test_nested_toml_sections_are_supported(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(
        """
[metadata]
tmdb_api_key = "abc123"
auto_lookup = true
fuzzy_threshold = 80

[paths]
staging_dir = "/tmp/staging"
movies_dir = "/tmp/movies"
tv_dir = "/tmp/tv"

[device]
path = "/dev/sr1"
auto_eject = false

[ripping]
min_main_length = 5400
min_extra_length = 60

[ui]
theme = "light"
""".strip()
    )
    monkeypatch.setattr(Settings, "CONFIG_PATH", config)

    settings = Settings()

    assert settings.tmdb_api_key == "abc123"
    assert settings.auto_lookup is True
    assert settings.fuzzy_threshold == 80
    assert settings.staging_dir == Path("/tmp/staging")
    assert settings.movies_dir == Path("/tmp/movies")
    assert settings.tv_dir == Path("/tmp/tv")
    assert settings.device == "/dev/sr1"
    assert settings.auto_eject is False
    assert settings.min_main_length == 5400
    assert settings.min_extra_length == 60
    assert settings.theme == "light"


def test_environment_overrides_toml(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(
        """
[device]
path = "/dev/sr0"
""".strip()
    )
    monkeypatch.setattr(Settings, "CONFIG_PATH", config)
    monkeypatch.setenv("RIPPER_DEVICE", "/dev/sr9")

    settings = Settings()

    assert settings.device == "/dev/sr9"
