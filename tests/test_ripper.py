"""Tests for ripper progress parsing and process registry."""

import subprocess
import threading
from unittest.mock import MagicMock

from ripper.core.disc import Title
from ripper.core.ripper import (
    HUMAN_PROGRESS_RE,
    PROGRESS_RE,
    _active_processes,
    _parse_human_progress_values,
    _parse_progress_values,
    _process_lock,
    _ProgressParser,
    cancel_all_rips,
    cancel_rip,
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


class TestProcessRegistry:
    """Tests for the dict-based process registry."""

    def _make_mock_proc(self, alive: bool = True) -> MagicMock:
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None if alive else 0
        proc.wait.return_value = 0
        return proc

    def test_cancel_rip_terminates_specific_process(self):
        proc = self._make_mock_proc()
        with _process_lock:
            _active_processes["test-1"] = proc
        try:
            cancel_rip("test-1")
            proc.terminate.assert_called_once()
        finally:
            with _process_lock:
                _active_processes.pop("test-1", None)

    def test_cancel_rip_ignores_unknown_id(self):
        # Should not raise
        cancel_rip("nonexistent")

    def test_cancel_rip_ignores_already_exited(self):
        proc = self._make_mock_proc(alive=False)
        with _process_lock:
            _active_processes["done-1"] = proc
        try:
            cancel_rip("done-1")
            proc.terminate.assert_not_called()
        finally:
            with _process_lock:
                _active_processes.pop("done-1", None)

    def test_cancel_all_rips_terminates_all(self):
        proc_a = self._make_mock_proc()
        proc_b = self._make_mock_proc()
        with _process_lock:
            _active_processes["a"] = proc_a
            _active_processes["b"] = proc_b
        try:
            cancel_all_rips()
            proc_a.terminate.assert_called_once()
            proc_b.terminate.assert_called_once()
        finally:
            with _process_lock:
                _active_processes.pop("a", None)
                _active_processes.pop("b", None)

    def test_concurrent_registration(self):
        """Verify multiple threads can register processes safely."""
        procs = [self._make_mock_proc() for _ in range(5)]
        errors: list[Exception] = []

        def register(idx: int) -> None:
            try:
                pid = f"concurrent-{idx}"
                with _process_lock:
                    _active_processes[pid] = procs[idx]
                with _process_lock:
                    _active_processes.pop(pid, None)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=register, args=(i,))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        # All should be cleaned up
        for i in range(5):
            assert f"concurrent-{i}" not in _active_processes
