"""Tests for extras classification and media type detection."""

from ripper.core.disc import ExtraType, MediaType, Title
from ripper.metadata.classifier import (
    classify_extra,
    classify_titles,
    detect_media_type,
)


class TestClassifyExtra:
    def test_behind_the_scenes(self):
        assert classify_extra("Behind the Scenes") == ExtraType.BEHIND_THE_SCENES

    def test_making_of(self):
        assert classify_extra("The Making of Dune") == ExtraType.BEHIND_THE_SCENES

    def test_deleted_scenes(self):
        assert classify_extra("Deleted Scenes") == ExtraType.DELETED_SCENES

    def test_extended_scene(self):
        assert classify_extra("Extended Scene") == ExtraType.DELETED_SCENES

    def test_featurette(self):
        assert classify_extra("Featurette: The World of Dune") == ExtraType.FEATURETTES

    def test_documentary(self):
        assert classify_extra("Documentary") == ExtraType.FEATURETTES

    def test_interview(self):
        assert classify_extra("Interview with Director") == ExtraType.INTERVIEWS

    def test_trailer(self):
        assert classify_extra("Theatrical Trailer") == ExtraType.TRAILERS

    def test_teaser(self):
        assert classify_extra("Teaser") == ExtraType.TRAILERS

    def test_unknown_falls_to_extras(self):
        assert classify_extra("Some Random Title") == ExtraType.EXTRAS

    def test_case_insensitive(self):
        assert classify_extra("BEHIND THE SCENES") == ExtraType.BEHIND_THE_SCENES


def _make_title(duration_seconds: int, name: str = "Title") -> Title:
    return Title(
        id=0,
        name=name,
        duration_seconds=duration_seconds,
        size_bytes=0,
        chapter_count=0,
    )


class TestDetectMediaType:
    def test_single_long_title_is_movie(self):
        titles = [_make_title(7200), _make_title(300), _make_title(150)]
        assert detect_media_type(titles) == MediaType.MOVIE

    def test_multiple_medium_titles_is_tv(self):
        titles = [
            _make_title(2700),
            _make_title(2580),
            _make_title(2640),
            _make_title(2700),
        ]
        assert detect_media_type(titles) == MediaType.TV_SHOW

    def test_short_titles_only_is_unknown(self):
        titles = [_make_title(300), _make_title(150)]
        assert detect_media_type(titles) == MediaType.UNKNOWN

    def test_multiple_long_titles_is_movie(self):
        # Multi-feature disc (e.g., theatrical + extended)
        titles = [_make_title(7200), _make_title(7800)]
        assert detect_media_type(titles) == MediaType.MOVIE


class TestClassifyTitles:
    def test_main_feature_flagged(self):
        titles = [_make_title(7200, "Feature"), _make_title(300, "Trailer")]
        classify_titles(titles)
        assert titles[0].is_main_feature is True
        assert titles[1].is_main_feature is False

    def test_extras_get_suggested_type(self):
        titles = [
            _make_title(7200, "Feature"),
            _make_title(2530, "Behind the Scenes"),
            _make_title(150, "Trailer"),
        ]
        classify_titles(titles)
        assert titles[1].suggested_extra_type == ExtraType.BEHIND_THE_SCENES
        assert titles[2].suggested_extra_type == ExtraType.TRAILERS
