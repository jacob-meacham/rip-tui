"""Application settings with Pydantic validation and TOML/env var support."""

from pathlib import Path
from typing import ClassVar

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ripper configuration loaded from env vars, TOML, or defaults."""

    model_config = SettingsConfigDict(
        env_prefix="RIPPER_",
    )

    CONFIG_PATH: ClassVar[Path] = (
        Path.home() / ".config" / "ripper" / "config.toml"
    )

    # Metadata
    tmdb_api_key: str = ""
    auto_lookup: bool = True
    fuzzy_threshold: int = Field(default=75, ge=0, le=100)

    # Paths
    staging_dir: Path = Path("/mnt/media/Rips-Staging")
    movies_dir: Path = Path("/mnt/media/Movies")
    tv_dir: Path = Path("/mnt/media/TV")

    # Device
    device: str = "/dev/sr0"
    auto_eject: bool = True

    # Ripping thresholds (seconds)
    min_main_length: int = Field(
        default=3600,
        description="Minimum seconds for main feature",
    )
    min_extra_length: int = Field(
        default=30,
        description="Skip titles shorter than this",
    )

    # UI
    theme: str = "dark"

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, **kwargs
    ):
        """Load from init kwargs, then env vars, then TOML file."""
        from pydantic_settings import (
            EnvSettingsSource,
            TomlConfigSettingsSource,
        )

        init_settings = kwargs.get("init_settings")
        sources = []
        if init_settings:
            sources.append(init_settings)
        sources.append(EnvSettingsSource(settings_cls))

        if cls.CONFIG_PATH.exists():
            sources.append(
                TomlConfigSettingsSource(
                    settings_cls,
                    toml_file=cls.CONFIG_PATH,
                )
            )

        return tuple(sources)
