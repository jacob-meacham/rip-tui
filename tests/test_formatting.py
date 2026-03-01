"""Tests for formatting utilities."""

from ripper.utils.formatting import (
    fmt_duration,
    fmt_rate,
    fmt_size,
    sanitize_filename,
)


class TestFmtDuration:
    def test_hours_minutes_seconds(self):
        assert fmt_duration(9015) == "2h 30m 15s"

    def test_zero(self):
        assert fmt_duration(0) == "0h 00m 00s"

    def test_minutes_only(self):
        assert fmt_duration(300) == "0h 05m 00s"

    def test_seconds_only(self):
        assert fmt_duration(45) == "0h 00m 45s"


class TestFmtSize:
    def test_gigabytes(self):
        assert fmt_size(34474836992) == "32.1 GB"

    def test_megabytes(self):
        assert fmt_size(4194304) == "4 MB"

    def test_bytes(self):
        assert fmt_size(512) == "512 bytes"

    def test_one_gb(self):
        assert fmt_size(1073741824) == "1.0 GB"


class TestFmtRate:
    def test_gb_per_second(self):
        assert fmt_rate(2_147_483_648) == "2.0 GB/s"

    def test_mb_per_second(self):
        assert fmt_rate(32_768_000) == "31.2 MB/s"

    def test_kb_per_second(self):
        assert fmt_rate(32_768) == "32 KB/s"

    def test_bytes_per_second(self):
        assert fmt_rate(512) == "512 B/s"


class TestSanitizeFilename:
    def test_replaces_colon(self):
        assert sanitize_filename(
            "Spider-Man: Into the Spider-Verse"
        ) == "Spider-Man - Into the Spider-Verse"

    def test_replaces_multiple_unsafe_chars(self):
        assert sanitize_filename('A\\B/C:D*E?F"G<H>I|J') == (
            "A-B-C-D-E-F-G-H-I-J"
        )

    def test_collapses_multiple_hyphens(self):
        assert sanitize_filename("A::B") == "A-B"

    def test_strips_leading_trailing_hyphens(self):
        assert sanitize_filename(":title:") == "title"

    def test_preserves_parentheses_and_year(self):
        assert sanitize_filename("Dune (2021)") == "Dune (2021)"

    def test_preserves_safe_characters(self):
        assert sanitize_filename("The Movie - Part 1") == (
            "The Movie - Part 1"
        )

    def test_empty_string(self):
        assert sanitize_filename("") == ""

    def test_real_world_title_with_colon(self):
        result = sanitize_filename(
            "Spider-Man: Into the Spider-Verse (2018)"
        )
        assert ":" not in result
        assert result == (
            "Spider-Man - Into the Spider-Verse (2018)"
        )
