"""Tests for formatting utilities."""

from ripper.utils.formatting import fmt_duration, fmt_size


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
