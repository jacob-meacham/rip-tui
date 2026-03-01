"""MKV filename to disc title matching utilities."""

from pathlib import Path

from ripper.core.disc import Title


def match_title_id(stem: str, title_id: int) -> bool:
    """Check if a lowercased filename stem matches a title ID pattern.

    Recognizes patterns: t00, title00, title_0
    """
    lower = stem.lower()
    patterns = [
        f"t{title_id:02d}",
        f"title{title_id:02d}",
        f"title_{title_id}",
    ]
    return any(p in lower for p in patterns)


def find_title_for_mkv(
    mkv: Path, titles: list[Title],
) -> Title | None:
    """Find the matching Title for an MKV file by filename pattern."""
    stem = mkv.stem
    for title in titles:
        if match_title_id(stem, title.id):
            return title
    return None
