"""Data structures for disc info, titles, and rip sessions."""

from dataclasses import dataclass
from enum import Enum, auto

from ripper.utils.formatting import fmt_duration, fmt_size


class MediaType(Enum):
    MOVIE = auto()
    TV_SHOW = auto()
    UNKNOWN = auto()


class ExtraType(Enum):
    """Emby-recognized extras folder names."""

    EXTRAS = "extras"
    BEHIND_THE_SCENES = "behind the scenes"
    DELETED_SCENES = "deleted scenes"
    FEATURETTES = "featurettes"
    INTERVIEWS = "interviews"
    SCENES = "scenes"
    SHORTS = "shorts"
    TRAILERS = "trailers"


@dataclass
class Title:
    """A single title on a disc."""

    id: int
    name: str
    duration_seconds: int
    size_bytes: int
    chapter_count: int
    is_main_feature: bool = False
    suggested_extra_type: ExtraType | None = None
    matched_episode: tuple[int, int] | None = None  # (season, episode)

    @property
    def duration_display(self) -> str:
        """Format duration as 'Xh XXm XXs'."""
        return fmt_duration(self.duration_seconds)

    @property
    def size_display(self) -> str:
        """Format size as human-readable string."""
        return fmt_size(self.size_bytes)


@dataclass
class DiscInfo:
    """Information about a scanned disc."""

    name: str
    device: str
    titles: list[Title]
    detected_media_type: MediaType = MediaType.UNKNOWN
    tmdb_id: int | None = None
    tmdb_title: str | None = None
    year: int | None = None

    @property
    def main_titles(self) -> list[Title]:
        return [t for t in self.titles if t.is_main_feature]

    @property
    def extra_titles(self) -> list[Title]:
        return [t for t in self.titles if not t.is_main_feature]
