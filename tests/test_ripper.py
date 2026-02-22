"""Tests for ripper progress parsing."""

from ripper.core.ripper import (
    HUMAN_PROGRESS_RE,
    PROGRESS_RE,
    _parse_human_progress_values,
    _parse_progress_values,
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
