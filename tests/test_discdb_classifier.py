"""Tests for TheDiscDB classification logic."""

from ripper.core.disc import ExtraType, MediaType, Title
from ripper.metadata.classifier import (
    apply_discdb_classifications,
    detect_media_type,
)


def _make_title(
    tid: int, name: str = "Title", duration: int = 300,
    source_file: str = "",
) -> Title:
    return Title(
        id=tid,
        name=name,
        duration_seconds=duration,
        size_bytes=0,
        chapter_count=0,
        source_file=source_file,
    )


class TestApplyDiscDbClassifications:
    def test_main_movie_flagged(self):
        titles = [_make_title(0, "Title 0", 7200, "00001.mpls")]
        discdb_titles = [
            {
                "index": 0,
                "source_file": "00001.mpls",
                "item_title": "Dune: Part Two",
                "item_type": "MainMovie",
                "season": None,
                "episode": None,
            }
        ]
        apply_discdb_classifications(titles, discdb_titles)
        assert titles[0].is_main_feature is True
        assert titles[0].discdb_info is not None
        assert titles[0].discdb_info.item_type == "MainMovie"

    def test_episode_mapped(self):
        titles = [
            _make_title(0, source_file="00010.mpls"),
            _make_title(1, source_file="00011.mpls"),
        ]
        discdb_titles = [
            {
                "index": 0,
                "source_file": "00010.mpls",
                "item_title": "Pilot",
                "item_type": "Episode",
                "season": 1,
                "episode": 1,
            },
            {
                "index": 1,
                "source_file": "00011.mpls",
                "item_title": "The Train Job",
                "item_type": "Episode",
                "season": 1,
                "episode": 2,
            },
        ]
        apply_discdb_classifications(titles, discdb_titles)
        assert titles[0].matched_episode == (1, 1)
        assert titles[1].matched_episode == (1, 2)

    def test_trailer_classified(self):
        titles = [_make_title(0, source_file="00050.mpls")]
        discdb_titles = [
            {
                "index": 0,
                "source_file": "00050.mpls",
                "item_title": "Theatrical Trailer",
                "item_type": "Trailer",
                "season": None,
                "episode": None,
            }
        ]
        apply_discdb_classifications(titles, discdb_titles)
        assert titles[0].suggested_extra_type == ExtraType.TRAILERS

    def test_deleted_scene_classified(self):
        titles = [_make_title(0, source_file="00060.mpls")]
        discdb_titles = [
            {
                "index": 0,
                "source_file": "00060.mpls",
                "item_title": "Deleted Scene 1",
                "item_type": "DeletedScene",
                "season": None,
                "episode": None,
            }
        ]
        apply_discdb_classifications(titles, discdb_titles)
        assert titles[0].suggested_extra_type == ExtraType.DELETED_SCENES

    def test_extra_uses_regex_refinement(self):
        titles = [_make_title(0, source_file="00070.mpls")]
        discdb_titles = [
            {
                "index": 0,
                "source_file": "00070.mpls",
                "item_title": "Behind the Scenes: Making Dune",
                "item_type": "Extra",
                "season": None,
                "episode": None,
            }
        ]
        apply_discdb_classifications(titles, discdb_titles)
        assert titles[0].suggested_extra_type == ExtraType.BEHIND_THE_SCENES

    def test_matches_by_source_file_not_index(self):
        """DiscDB indices differ from MakeMKV IDs; match by source file."""
        # MakeMKV title IDs 0-3 don't match DiscDB indices 1-3
        titles = [
            _make_title(0, "Disc Name", 216, source_file="00800.mpls"),
            _make_title(1, "Disc Name", 7010, source_file="00249.mpls"),
            _make_title(2, "Disc Name", 7010, source_file="00250.mpls"),
            _make_title(3, "Disc Name", 6325, source_file="00251.mpls"),
        ]
        titles[1].is_main_feature = True
        titles[2].is_main_feature = True
        titles[3].is_main_feature = True

        # DiscDB has different index numbers, only 1 of 3 is MainMovie
        discdb_titles = [
            {
                "index": 1,
                "source_file": "00249.mpls",
                "item_title": "Spider-Man: Into the Spider-Verse",
                "item_type": "MainMovie",
                "season": None,
                "episode": None,
            },
            {
                "index": 2,
                "source_file": "00339.m2ts",
                "item_title": "Spider-Ham: Caught in a Ham",
                "item_type": "Extra",
                "season": None,
                "episode": None,
            },
            {
                "index": 3,
                "source_file": "00340.m2ts",
                "item_title": "Credits and After Credits Scene",
                "item_type": "Extra",
                "season": None,
                "episode": None,
            },
        ]
        apply_discdb_classifications(titles, discdb_titles)

        # Title 1 matched by source_file "00249.mpls"
        assert titles[1].is_main_feature is True
        assert titles[1].discdb_info.item_title == (
            "Spider-Man: Into the Spider-Verse"
        )

        # Titles 2, 3 have no source_file match, but since DiscDB
        # identifies a MainMovie, all duration-based mains are demoted
        assert titles[2].is_main_feature is False
        assert titles[3].is_main_feature is False
        assert titles[2].discdb_info is None
        assert titles[3].discdb_info is None

    def test_stem_match_mpls_vs_m2ts(self):
        """Match DiscDB .m2ts source files to MakeMKV .mpls titles."""
        titles = [
            _make_title(0, "Disc Name", 241, source_file="00339.mpls"),
            _make_title(1, "Disc Name", 684, source_file="00340.mpls"),
        ]
        discdb_titles = [
            {
                "index": 10,
                "source_file": "00339.m2ts",
                "item_title": "Spider-Ham: Caught in a Ham",
                "item_type": "Extra",
                "season": None,
                "episode": None,
            },
            {
                "index": 11,
                "source_file": "00340.m2ts",
                "item_title": "Credits and After Credits Scene",
                "item_type": "Extra",
                "season": None,
                "episode": None,
            },
        ]
        apply_discdb_classifications(titles, discdb_titles)

        assert titles[0].discdb_info.item_title == (
            "Spider-Ham: Caught in a Ham"
        )
        assert titles[1].discdb_info.item_title == (
            "Credits and After Credits Scene"
        )
        assert titles[0].is_main_feature is False
        assert titles[1].is_main_feature is False

    def test_unmatched_titles_unchanged(self):
        titles = [
            _make_title(0, source_file="00001.mpls"),
            _make_title(5, source_file="00099.mpls"),
        ]
        discdb_titles = [
            {
                "index": 0,
                "source_file": "00001.mpls",
                "item_title": "Feature",
                "item_type": "MainMovie",
                "season": None,
                "episode": None,
            }
        ]
        apply_discdb_classifications(titles, discdb_titles)
        # Title 5 has no match, should be untouched
        assert titles[1].discdb_info is None
        assert titles[1].is_main_feature is False

    def test_unknown_type_falls_back_to_regex(self):
        titles = [_make_title(0, source_file="00080.mpls")]
        discdb_titles = [
            {
                "index": 0,
                "source_file": "00080.mpls",
                "item_title": "Interview with Director",
                "item_type": "SomeNewType",
                "season": None,
                "episode": None,
            }
        ]
        apply_discdb_classifications(titles, discdb_titles)
        assert titles[0].suggested_extra_type == ExtraType.INTERVIEWS


class TestDetectMediaTypeWithDiscDb:
    def test_movie_from_discdb(self):
        titles = [_make_title(0, duration=300)]
        assert (
            detect_media_type(titles, discdb_type="Movie")
            == MediaType.MOVIE
        )

    def test_series_from_discdb(self):
        titles = [_make_title(0, duration=300)]
        assert (
            detect_media_type(titles, discdb_type="Series")
            == MediaType.TV_SHOW
        )

    def test_unknown_discdb_type_falls_through(self):
        titles = [_make_title(0, duration=7200)]
        assert (
            detect_media_type(titles, discdb_type="Unknown")
            == MediaType.MOVIE
        )

    def test_none_discdb_type_uses_heuristics(self):
        titles = [_make_title(0, duration=300)]
        assert (
            detect_media_type(titles, discdb_type=None)
            == MediaType.UNKNOWN
        )
