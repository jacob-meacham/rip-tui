"""Classify disc titles as main features, extras types, or TV episodes."""

import re

from ripper.core.disc import ExtraType, MediaType, Title

# Patterns for auto-classifying extras by title name
EXTRA_PATTERNS: list[tuple[re.Pattern[str], ExtraType]] = [
    (
        re.compile(
            r"behind the scenes|making of|bts|how .* made",
            re.IGNORECASE,
        ),
        ExtraType.BEHIND_THE_SCENES,
    ),
    (
        re.compile(r"deleted|extended scene|alternate", re.IGNORECASE),
        ExtraType.DELETED_SCENES,
    ),
    (
        re.compile(
            r"featurette|documentary|special feature",
            re.IGNORECASE,
        ),
        ExtraType.FEATURETTES,
    ),
    (
        re.compile(r"interview|q\s*&\s*a|conversation", re.IGNORECASE),
        ExtraType.INTERVIEWS,
    ),
    (
        re.compile(r"trailer|teaser|preview", re.IGNORECASE),
        ExtraType.TRAILERS,
    ),
    (
        re.compile(r"short film|short$", re.IGNORECASE),
        ExtraType.SHORTS,
    ),
    (
        re.compile(r"scene\b", re.IGNORECASE),
        ExtraType.SCENES,
    ),
]


def classify_extra(title_name: str) -> ExtraType:
    """Classify an extra by its title name using pattern matching.

    Returns the best matching ExtraType, or EXTRAS as fallback.
    """
    for pattern, extra_type in EXTRA_PATTERNS:
        if pattern.search(title_name):
            return extra_type
    return ExtraType.EXTRAS


def classify_titles(titles: list[Title], min_main_length: int = 3600) -> None:
    """Classify all titles on a disc in-place.

    Sets is_main_feature and suggested_extra_type on each title.
    """
    for title in titles:
        if title.duration_seconds >= min_main_length:
            title.is_main_feature = True
        else:
            title.is_main_feature = False
            title.suggested_extra_type = classify_extra(title.name)


def detect_media_type(titles: list[Title], min_main_length: int = 3600) -> MediaType:
    """Detect whether a disc is a movie or TV show based on title patterns.

    Heuristics:
    - Single long title (>= min_main_length) with shorter extras -> MOVIE
    - Multiple titles of similar length (20-60 min) -> TV_SHOW
    - Otherwise -> UNKNOWN
    """
    long_titles = [t for t in titles if t.duration_seconds >= min_main_length]
    medium_titles = [t for t in titles if 1200 <= t.duration_seconds < min_main_length]

    # Single long title = movie
    if len(long_titles) == 1:
        return MediaType.MOVIE

    # Multiple long titles could be multi-feature disc
    if len(long_titles) > 1:
        return MediaType.MOVIE

    # Multiple medium-length titles (20-60 min) suggests TV episodes
    if len(medium_titles) >= 3:
        return MediaType.TV_SHOW

    return MediaType.UNKNOWN
