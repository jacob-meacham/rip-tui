"""Fuzzy title matching for disc names against TMDb results."""

import logging
import re

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# Patterns to strip from disc names for cleaner matching
DISC_NOISE_PATTERNS = [
    r"_DISC_?\d+",
    r"_D\d+",
    r"_BD\d*",
    r"_UHD",
    r"_4K",
    r"_BLURAY",
    r"_BLU_?RAY",
    r"_RETAIL",
    r"_SPECIAL_EDITION",
    r"_EXTENDED",
    r"_DIRECTORS_CUT",
]


def clean_disc_name(disc_name: str) -> str:
    """Clean a raw disc name into a searchable title.

    Example: "DUNE_PART_TWO_DISC_1" -> "Dune Part Two"
    """
    name = disc_name.upper()
    for pattern in DISC_NOISE_PATTERNS:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE)

    # Replace underscores and multiple spaces
    name = name.replace("_", " ")
    name = re.sub(r"\s+", " ", name).strip()

    # Title case
    return name.title()


def match_title(
    disc_name: str,
    candidates: list[dict],
    title_key: str = "title",
    threshold: int = 75,
) -> dict | None:
    """Find the best matching title from TMDb candidates.

    Args:
        disc_name: Cleaned disc name to match against.
        candidates: List of TMDb result dicts.
        title_key: Key in candidate dicts containing the title
            ("title" for movies, "name" for TV).
        threshold: Minimum fuzzy match score (0-100).

    Returns:
        Best matching candidate dict, or None if no match above threshold.
    """
    if not candidates:
        return None

    best_match: dict | None = None
    best_score = 0

    for candidate in candidates:
        candidate_title = candidate.get(title_key, "")
        score = fuzz.WRatio(disc_name, candidate_title)

        if score > best_score:
            best_score = score
            best_match = candidate

    if best_score >= threshold and best_match is not None:
        logger.info(
            "Matched '%s' -> '%s' (score: %d)",
            disc_name,
            best_match.get(title_key),
            best_score,
        )
        return best_match

    logger.info(
        "No match above threshold %d for '%s' (best: %d)",
        threshold,
        disc_name,
        best_score,
    )
    return None


def match_episodes_by_duration(
    title_durations: list[tuple[int, int]],
    episode_runtimes: list[tuple[int, int]],
    tolerance_seconds: int = 120,
) -> dict[int, int]:
    """Match disc titles to TV episodes by duration.

    Uses greedy matching with tolerance window.

    Args:
        title_durations: List of (title_id, duration_seconds) from disc.
        episode_runtimes: List of (episode_number, runtime_seconds) from TMDb.
        tolerance_seconds: Allowed difference in seconds.

    Returns:
        Dict mapping title_id -> episode_number.
    """
    matches: dict[int, int] = {}
    used_episodes: set[int] = set()

    # Sort titles by duration descending for better matching
    sorted_titles = sorted(title_durations, key=lambda t: t[1], reverse=True)
    sorted_episodes = sorted(episode_runtimes, key=lambda e: e[1], reverse=True)

    for title_id, title_dur in sorted_titles:
        best_ep: int | None = None
        best_diff = float("inf")

        for ep_num, ep_dur in sorted_episodes:
            if ep_num in used_episodes:
                continue
            diff = abs(title_dur - ep_dur)
            if diff <= tolerance_seconds and diff < best_diff:
                best_diff = diff
                best_ep = ep_num

        if best_ep is not None:
            matches[title_id] = best_ep
            used_episodes.add(best_ep)
            logger.info(
                "Title %d matched to episode %d (diff: %ds)",
                title_id,
                best_ep,
                best_diff,
            )

    return matches
