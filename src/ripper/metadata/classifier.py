"""Classify disc titles as main features, extras types, or TV episodes."""

import logging
import re

from ripper.core.disc import DiscDbTitleInfo, ExtraType, MediaType, Title

logger = logging.getLogger(__name__)

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


def apply_discdb_classifications(
    titles: list[Title], discdb_titles: list[dict]
) -> None:
    """Apply TheDiscDB title classifications in-place.

    Matches DiscDB entries to titles by source file name (exact match,
    then stem match for mpls/m2ts differences).

    Args:
        titles: Disc titles to classify.
        discdb_titles: Title dicts from DiscDbClient.lookup_disc().
    """
    # Build source-file lookup for DiscDB entries
    by_source = {}
    by_stem = {}
    for dt in discdb_titles:
        sf = dt.get("source_file", "")
        if sf:
            by_source[sf] = dt
            by_stem[sf.rsplit(".", 1)[0]] = dt
        logger.debug(
            "DiscDB title: index=%s source_file=%r type=%s title=%r",
            dt.get("index"), sf, dt.get("item_type"), dt.get("item_title"),
        )

    # If DiscDB identifies any MainMovie, demote all duration-based
    # main features first — only DiscDB MainMovie titles should be main.
    has_discdb_main = any(
        dt.get("item_type") == "MainMovie" for dt in discdb_titles
    )
    if has_discdb_main:
        for title in titles:
            if title.is_main_feature:
                title.is_main_feature = False
                title.suggested_extra_type = classify_extra(title.name)

    for title in titles:
        # Match by source file, then by stem (mpls vs m2ts)
        match = by_source.get(title.source_file)
        if match is None and title.source_file:
            stem = title.source_file.rsplit(".", 1)[0]
            match = by_stem.get(stem)
        if match is None:
            logger.debug(
                "Title %d: source_file=%r — no DiscDB match",
                title.id, title.source_file,
            )
            continue
        logger.debug(
            "Title %d: source_file=%r matched DiscDB %r (%s)",
            title.id, title.source_file,
            match.get("source_file"), match.get("item_type"),
        )

        title.discdb_info = DiscDbTitleInfo(
            source_file=match.get("source_file", ""),
            item_title=match.get("item_title", ""),
            item_type=match.get("item_type", ""),
            season=match.get("season"),
            episode=match.get("episode"),
        )

        item_type = match.get("item_type", "")
        if item_type == "MainMovie":
            title.is_main_feature = True
            title.suggested_extra_type = None
        else:
            title.is_main_feature = False

            if item_type == "Episode":
                season = match.get("season")
                episode = match.get("episode")
                if season is not None and episode is not None:
                    title.matched_episode = (season, episode)
            elif item_type == "Trailer":
                title.suggested_extra_type = ExtraType.TRAILERS
            elif item_type == "DeletedScene":
                title.suggested_extra_type = ExtraType.DELETED_SCENES
            else:
                # "Extra" or unknown type — classify by curated title
                item_title = match.get("item_title", "")
                title.suggested_extra_type = classify_extra(item_title)


def detect_media_type(
    titles: list[Title],
    min_main_length: int = 3600,
    discdb_type: str | None = None,
) -> MediaType:
    """Detect whether a disc is a movie or TV show based on title patterns.

    If discdb_type is provided, maps "Movie" -> MOVIE, "Series" -> TV_SHOW
    directly before falling through to heuristics.

    Heuristics:
    - Single long title (>= min_main_length) with shorter extras -> MOVIE
    - Multiple titles of similar length (20-60 min) -> TV_SHOW
    - Otherwise -> UNKNOWN
    """
    if discdb_type:
        discdb_map = {"Movie": MediaType.MOVIE, "Series": MediaType.TV_SHOW}
        if discdb_type in discdb_map:
            return discdb_map[discdb_type]

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
