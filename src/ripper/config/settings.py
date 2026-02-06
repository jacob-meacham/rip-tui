"""Application settings with Pydantic validation and TOML/env var support."""

import tomllib
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
    def _load_toml_settings(cls) -> dict:
        """Load config TOML and normalize nested sections."""
        if not cls.CONFIG_PATH.exists():
            return {}

        with cls.CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)

        if not isinstance(data, dict):
            return {}

        flat_keys = {
            "tmdb_api_key",
            "auto_lookup",
            "fuzzy_threshold",
            "staging_dir",
            "movies_dir",
            "tv_dir",
            "device",
            "auto_eject",
            "min_main_length",
            "min_extra_length",
            "theme",
        }
        normalized = {
            k: v
            for k, v in data.items()
            if k in flat_keys and not isinstance(v, dict)
        }

        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            for key in ("tmdb_api_key", "auto_lookup", "fuzzy_threshold"):
                if key in metadata:
                    normalized[key] = metadata[key]

        paths = data.get("paths")
        if isinstance(paths, dict):
            for key in ("staging_dir", "movies_dir", "tv_dir"):
                if key in paths:
                    normalized[key] = paths[key]

        device = data.get("device")
        if isinstance(device, dict):
            if "path" in device:
                normalized["device"] = device["path"]
            if "device" in device:
                normalized["device"] = device["device"]
            if "auto_eject" in device:
                normalized["auto_eject"] = device["auto_eject"]

        ripping = data.get("ripping")
        if isinstance(ripping, dict):
            for key in ("min_main_length", "min_extra_length"):
                if key in ripping:
                    normalized[key] = ripping[key]

        ui = data.get("ui")
        if isinstance(ui, dict) and "theme" in ui:
            normalized["theme"] = ui["theme"]

        return normalized

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, **kwargs
    ):
        """Load from init kwargs, then env vars, then TOML file."""
        from pydantic_settings import EnvSettingsSource

        init_settings = kwargs.get("init_settings")
        env_settings = kwargs.get("env_settings")
        sources = []
        if init_settings:
            sources.append(init_settings)
        if env_settings:
            sources.append(env_settings)
        else:
            sources.append(EnvSettingsSource(settings_cls))

        sources.append(cls._load_toml_settings)

        return tuple(sources)
