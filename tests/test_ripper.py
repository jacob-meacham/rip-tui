"""Tests for ripper progress parsing."""

from ripper.core.ripper import PROGRESS_RE, _parse_progress_values


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
