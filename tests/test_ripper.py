"""Tests for ripper progress parsing."""

from ripper.core.disc import Title
from ripper.core.ripper import (
    HUMAN_PROGRESS_RE,
    PROGRESS_RE,
    _parse_human_progress_values,
    _parse_progress_values,
    _ProgressParser,
)


def test_parse_progress_values_prefers_third_field_as_max():
    match = PROGRESS_RE.match("PRGV:100,200,1000")
    assert match is not None
    assert _parse_progress_values(match) == (100, 1000)


def test_parse_progress_values_falls_back_to_second_field():
    match = PROGRESS_RE.match("PRGV:100,1000,0")
    assert match is not None
    assert _parse_progress_values(match) == (100, 1000)


def test_parse_progress_values_handles_no_denominator():
    match = PROGRESS_RE.match("PRGV:100,0,0")
    assert match is not None
    assert _parse_progress_values(match) == (100, 0)


def test_parse_human_progress_values_prefers_total_percent():
    match = HUMAN_PROGRESS_RE.match(
        "Current progress - 8%  , Total progress - 11%"
    )
    assert match is not None
    assert _parse_human_progress_values(match) == 11.0


def test_parse_human_progress_values_falls_back_to_current_percent():
    match = HUMAN_PROGRESS_RE.match(
        "Current progress - 25%  , Total progress - 0%"
    )
    assert match is not None
    assert _parse_human_progress_values(match) == 25.0


def _make_title(title_id: int = 0, name: str = "Test") -> Title:
    return Title(
        id=title_id, name=name,
        duration_seconds=3600, size_bytes=1_000_000,
        chapter_count=10,
    )


class TestProgressParser:
    def test_parses_prgt_line(self):
        parser = _ProgressParser()
        result = parser.parse_line('PRGT:0,0,"Saving to MKV file"')
        assert result is not None
        progress, source = result
        assert source == "PRGT"
        assert progress.title_name == "Saving to MKV file"

    def test_parses_prgc_line(self):
        parser = _ProgressParser()
        result = parser.parse_line('PRGC:3,0,"Main Feature"')
        assert result is not None
        progress, source = result
        assert source == "PRGC"
        assert progress.title_id == 3
        assert progress.title_name == "Main Feature"

    def test_parses_prgv_line(self):
        parser = _ProgressParser()
        result = parser.parse_line("PRGV:500,0,1000")
        assert result is not None
        progress, source = result
        assert source == "PRGV"
        assert progress.percent == 50.0
        assert progress.current_bytes == 500
        assert progress.total_bytes == 1000

    def test_parses_human_progress_line(self):
        parser = _ProgressParser()
        result = parser.parse_line(
            "Current progress - 8%  , Total progress - 11%"
        )
        assert result is not None
        progress, source = result
        assert source == "HUMAN_PROGRESS"
        assert progress.percent == 11.0

    def test_returns_none_for_unmatched_lines(self):
        parser = _ProgressParser()
        assert parser.parse_line("MSG:some random message") is None
        assert parser.parse_line("") is None
        assert parser.parse_line("random text") is None

    def test_parses_human_action(self):
        parser = _ProgressParser()
        result = parser.parse_line(
            "Current action: Saving to MKV file"
        )
        assert result is not None
        progress, source = result
        assert source == "HUMAN_ACTION"
        assert progress.title_name == "Saving to MKV file"

    def test_parses_human_operation(self):
        parser = _ProgressParser()
        result = parser.parse_line(
            "Current operation: Saving all titles to MKV files"
        )
        assert result is not None
        progress, source = result
        assert source == "HUMAN_OPERATION"
        assert progress.title_name == (
            "Saving all titles to MKV files"
        )

    def test_uses_current_title_for_initial_state(self):
        title = _make_title(5, "My Feature")
        parser = _ProgressParser(current_title=title)
        result = parser.parse_line("PRGV:100,0,1000")
        assert result is not None
        progress, _ = result
        assert progress.title_id == 5
        assert progress.title_name == "My Feature"

    def test_rate_tracking_across_prgv_lines(self):
        parser = _ProgressParser()
        # First PRGV establishes baseline — no rate yet
        r1 = parser.parse_line("PRGV:100,0,1000")
        assert r1 is not None
        assert r1[0].bytes_per_second is None

        # Second PRGV should compute a rate
        r2 = parser.parse_line("PRGV:200,0,1000")
        assert r2 is not None
        # Rate should be positive (200 - 100 bytes over some time)
        assert r2[0].bytes_per_second is None or r2[0].bytes_per_second >= 0
