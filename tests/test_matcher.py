"""Tests for title matching and disc name cleaning."""

from ripper.metadata.matcher import (
    clean_disc_name,
    match_episodes_by_duration,
    match_title,
)


class TestCleanDiscName:
    def test_underscores_to_spaces(self):
        assert clean_disc_name("DUNE_PART_TWO") == "Dune Part Two"

    def test_strips_disc_number(self):
        assert clean_disc_name("DUNE_PART_TWO_DISC_1") == "Dune Part Two"

    def test_strips_uhd(self):
        assert clean_disc_name("INCEPTION_UHD") == "Inception"

    def test_strips_bluray(self):
        assert clean_disc_name("INCEPTION_BLURAY") == "Inception"

    def test_strips_4k(self):
        assert clean_disc_name("INCEPTION_4K") == "Inception"

    def test_complex_name(self):
        result = clean_disc_name("THE_LORD_OF_THE_RINGS_DISC_2_BD")
        assert result == "The Lord Of The Rings"

    def test_already_clean(self):
        assert clean_disc_name("INCEPTION") == "Inception"


class TestMatchTitle:
    def test_exact_match(self):
        candidates = [
            {"title": "Dune: Part Two", "id": 1},
            {"title": "Dune", "id": 2},
        ]
        result = match_title("Dune Part Two", candidates)
        assert result is not None
        assert result["id"] == 1

    def test_fuzzy_match(self):
        candidates = [
            {"title": "Dune: Part Two", "id": 1},
            {"title": "The Hunger Games", "id": 2},
        ]
        result = match_title("Dune Pt Two", candidates)
        assert result is not None
        assert result["id"] == 1

    def test_no_match_below_threshold(self):
        candidates = [
            {"title": "Completely Different Movie", "id": 1},
        ]
        result = match_title("Dune Part Two", candidates, threshold=80)
        assert result is None

    def test_empty_candidates(self):
        assert match_title("Dune", []) is None

    def test_tv_show_name_key(self):
        candidates = [
            {"name": "Breaking Bad", "id": 1},
            {"name": "Better Call Saul", "id": 2},
        ]
        result = match_title("Breaking Bad", candidates, title_key="name")
        assert result is not None
        assert result["id"] == 1


class TestMatchEpisodesByDuration:
    def test_exact_match(self):
        titles = [(0, 2700), (1, 2580), (2, 2640)]  # 45m, 43m, 44m
        episodes = [(1, 2700), (2, 2580), (3, 2640)]
        result = match_episodes_by_duration(titles, episodes)
        assert result == {0: 1, 1: 2, 2: 3}

    def test_within_tolerance(self):
        titles = [(0, 2700), (1, 2600)]
        episodes = [(1, 2750), (2, 2550)]  # within 120s
        result = match_episodes_by_duration(titles, episodes, tolerance_seconds=120)
        assert result == {0: 1, 1: 2}

    def test_no_match_outside_tolerance(self):
        titles = [(0, 2700)]
        episodes = [(1, 1800)]  # 15 min difference
        result = match_episodes_by_duration(titles, episodes, tolerance_seconds=120)
        assert result == {}

    def test_more_titles_than_episodes(self):
        titles = [(0, 2700), (1, 2580), (2, 900)]  # 3rd is short
        episodes = [(1, 2700), (2, 2580)]
        result = match_episodes_by_duration(titles, episodes)
        assert len(result) == 2
        assert 2 not in result
