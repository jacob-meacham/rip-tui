"""Data structures for disc info, titles, and rip sessions."""

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path


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
        h = self.duration_seconds // 3600
        m = (self.duration_seconds % 3600) // 60
        s = self.duration_seconds % 60
        return f"{h}h {m:02d}m {s:02d}s"

    @property
    def size_display(self) -> str:
        """Format size as human-readable string."""
        if self.size_bytes >= 1_073_741_824:
            return f"{self.size_bytes / 1_073_741_824:.1f} GB"
        elif self.size_bytes >= 1_048_576:
            return f"{self.size_bytes / 1_048_576:.0f} MB"
        return f"{self.size_bytes} bytes"


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


@dataclass
class RipSession:
    """Tracks multi-disc rip state for resume support."""

    session_id: str
    movie_name: str
    total_discs: int
    completed_discs: list[int] = field(default_factory=list)
    ripped_files: dict[int, list[Path]] = field(default_factory=dict)
