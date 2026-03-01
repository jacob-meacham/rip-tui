"""Tests for MKV-to-title matching utilities."""

from pathlib import Path

from ripper.core.disc import Title
from ripper.utils.matching import find_title_for_mkv, match_title_id


def _make_title(title_id: int, name: str = "Test") -> Title:
    return Title(
        id=title_id,
        name=name,
        duration_seconds=3600,
        size_bytes=1_000_000,
        chapter_count=10,
    )


class TestMatchTitleId:
    def test_matches_t_pattern(self):
        assert match_title_id("t00", 0) is True

    def test_matches_t_pattern_with_prefix(self):
        assert match_title_id("disc_t03_feature", 3) is True

    def test_matches_title_pattern(self):
        assert match_title_id("title05", 5) is True

    def test_matches_title_underscore_pattern(self):
        assert match_title_id("title_12", 12) is True

    def test_case_insensitive(self):
        assert match_title_id("T03", 3) is True
        assert match_title_id("Title05", 5) is True

    def test_no_match(self):
        assert match_title_id("something_else", 5) is False

    def test_wrong_id(self):
        assert match_title_id("t03", 5) is False

    def test_zero_padded(self):
        assert match_title_id("t01", 1) is True
        assert match_title_id("title01", 1) is True


class TestFindTitleForMkv:
    def test_finds_matching_title(self):
        titles = [_make_title(0, "Intro"), _make_title(3, "Feature")]
        mkv = Path("/tmp/t03.mkv")
        result = find_title_for_mkv(mkv, titles)
        assert result is not None
        assert result.id == 3

    def test_returns_none_when_no_match(self):
        titles = [_make_title(0), _make_title(1)]
        mkv = Path("/tmp/unknown.mkv")
        result = find_title_for_mkv(mkv, titles)
        assert result is None

    def test_empty_titles_list(self):
        mkv = Path("/tmp/t00.mkv")
        assert find_title_for_mkv(mkv, []) is None

    def test_matches_title_pattern(self):
        titles = [_make_title(5, "Bonus")]
        mkv = Path("/tmp/title05.mkv")
        result = find_title_for_mkv(mkv, titles)
        assert result is not None
        assert result.id == 5

    def test_matches_title_underscore_pattern(self):
        titles = [_make_title(12, "Extra")]
        mkv = Path("/tmp/title_12.mkv")
        result = find_title_for_mkv(mkv, titles)
        assert result is not None
        assert result.id == 12

    def test_returns_first_match(self):
        titles = [_make_title(0, "First"), _make_title(0, "Second")]
        mkv = Path("/tmp/t00.mkv")
        result = find_title_for_mkv(mkv, titles)
        assert result is not None
        assert result.name == "First"
