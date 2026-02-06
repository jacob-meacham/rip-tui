"""Tests for disc scanner output parsing."""

from ripper.config.settings import Settings
from ripper.core.scanner import _parse_duration, _parse_makemkv_output, _parse_size


class TestParseDuration:
    def test_hours_minutes_seconds(self):
        assert _parse_duration("2:30:15") == 9015

    def test_zero_duration(self):
        assert _parse_duration("0:00:00") == 0

    def test_minutes_seconds_only(self):
        assert _parse_duration("45:30") == 2730

    def test_short_duration(self):
        assert _parse_duration("0:00:05") == 5


class TestParseSize:
    def test_numeric_string(self):
        assert _parse_size("1073741824") == 1073741824

    def test_with_units(self):
        assert _parse_size("1234 bytes") == 1234

    def test_empty(self):
        assert _parse_size("") == 0

    def test_non_numeric(self):
        assert _parse_size("no numbers") == 0


class TestParseMakemkvOutput:
    """Test parsing of raw makemkvcon output."""

    SAMPLE_OUTPUT = """\
CINFO:1,6209,"Blu-ray disc"
CINFO:2,0,"DUNE_PART_TWO"
CINFO:30,0,""
CINFO:31,6119,"<b>Source information</b><br>"
CINFO:32,0,"DUNE_PART_TWO"
TINFO:0,2,0,"Dune Part Two"
TINFO:0,8,0,"18"
TINFO:0,9,0,"2:46:06"
TINFO:0,10,0,"34474836992"
TINFO:0,11,0,"34474836992"
TINFO:1,2,0,"Behind the Scenes"
TINFO:1,8,0,"8"
TINFO:1,9,0,"0:42:15"
TINFO:1,10,0,"4513218560"
TINFO:1,11,0,"4513218560"
TINFO:2,2,0,"Trailer"
TINFO:2,8,0,"1"
TINFO:2,9,0,"0:02:30"
TINFO:2,10,0,"322122752"
TINFO:2,11,0,"322122752"
TINFO:3,2,0,"Menu Loop"
TINFO:3,8,0,"0"
TINFO:3,9,0,"0:00:10"
TINFO:3,10,0,"1048576"
TINFO:3,11,0,"1048576"
"""

    def _settings(self, tmp_path) -> Settings:
        return Settings(
            staging_dir=tmp_path / "staging",
            movies_dir=tmp_path / "movies",
            tv_dir=tmp_path / "tv",
            device="/dev/null",
            tmdb_api_key="",
            min_main_length=3600,
            min_extra_length=30,
        )

    def test_disc_name_parsed(self, tmp_path):
        settings = self._settings(tmp_path)
        disc = _parse_makemkv_output(self.SAMPLE_OUTPUT, settings)
        assert disc.name == "DUNE_PART_TWO"

    def test_title_count_excludes_short(self, tmp_path):
        settings = self._settings(tmp_path)
        disc = _parse_makemkv_output(self.SAMPLE_OUTPUT, settings)
        # Title 3 (10s) is below min_extra_length (30s), so excluded
        assert len(disc.titles) == 3

    def test_main_feature_detected(self, tmp_path):
        settings = self._settings(tmp_path)
        disc = _parse_makemkv_output(self.SAMPLE_OUTPUT, settings)
        main = [t for t in disc.titles if t.is_main_feature]
        assert len(main) == 1
        assert main[0].name == "Dune Part Two"
        assert main[0].duration_seconds == 9966  # 2h 46m 6s

    def test_extras_detected(self, tmp_path):
        settings = self._settings(tmp_path)
        disc = _parse_makemkv_output(self.SAMPLE_OUTPUT, settings)
        extras = [t for t in disc.titles if not t.is_main_feature]
        assert len(extras) == 2
        names = {t.name for t in extras}
        assert "Behind the Scenes" in names
        assert "Trailer" in names

    def test_title_sizes_parsed(self, tmp_path):
        settings = self._settings(tmp_path)
        disc = _parse_makemkv_output(self.SAMPLE_OUTPUT, settings)
        main = disc.titles[0]
        assert main.size_bytes == 34474836992

    def test_chapter_counts(self, tmp_path):
        settings = self._settings(tmp_path)
        disc = _parse_makemkv_output(self.SAMPLE_OUTPUT, settings)
        assert disc.titles[0].chapter_count == 18
        assert disc.titles[1].chapter_count == 8
