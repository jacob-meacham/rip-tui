"""Tests for progress debug trace summarization."""

import json
from pathlib import Path

from ripper.core.ripper import summarize_progress_trace


def _write_trace(path: Path, rows: list[dict[str, object] | str]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            if isinstance(row, str):
                stream.write(row)
            else:
                stream.write(json.dumps(row, ensure_ascii=True))
            stream.write("\n")


def test_summarize_progress_trace_counts_progress_events(tmp_path) -> None:
    trace = tmp_path / "progress.jsonl"
    _write_trace(
        trace,
        [
            {"event": "session_start"},
            {
                "event": "raw_line",
                "line": 'PRGT:0,0,"Saving to MKV file"',
            },
            {"event": "line_parsed", "kind": "PRGT"},
            {
                "event": "progress_emit",
                "source": "PRGT",
                "progress": {
                    "title_name": "Saving to MKV file",
                    "percent": 0.0,
                    "current_bytes": 0,
                    "total_bytes": 0,
                },
            },
            {"event": "raw_line", "line": "PRGV:50,0,100"},
            {"event": "line_parsed", "kind": "PRGV"},
            {
                "event": "progress_emit",
                "source": "PRGV",
                "progress": {
                    "title_name": "Saving to MKV file",
                    "percent": 50.0,
                    "current_bytes": 50,
                    "total_bytes": 100,
                },
            },
            {"event": "unparsed_progress_line", "line": "PRXX:1,2,3"},
            {"event": "process_exit", "return_code": 0},
            {"event": "session_end"},
        ],
    )

    summary = summarize_progress_trace(trace, tail_size=5)

    assert summary["total_events"] == 10
    assert summary["raw_lines"] == 2
    assert summary["malformed_lines"] == 0
    assert summary["parsed_counts"] == {"PRGT": 1, "PRGV": 1}
    assert summary["emitted_counts"] == {"PRGT": 1, "PRGV": 1}
    assert summary["process_exit_code"] == 0
    assert summary["unparsed_progress_lines"] == ["PRXX:1,2,3"]
    assert summary["final_progress"] == {
        "title_name": "Saving to MKV file",
        "percent": 50.0,
        "current_bytes": 50,
        "total_bytes": 100,
    }


def test_summarize_progress_trace_handles_malformed_lines(tmp_path) -> None:
    trace = tmp_path / "progress.jsonl"
    _write_trace(
        trace,
        [
            {"event": "raw_line", "line": "first"},
            "{bad json",
            {"event": "raw_line", "line": "second"},
            {"event": "raw_line", "line": "third"},
        ],
    )

    summary = summarize_progress_trace(trace, tail_size=2)

    assert summary["total_events"] == 3
    assert summary["malformed_lines"] == 1
    assert summary["raw_lines"] == 3
    assert summary["raw_tail"] == ["second", "third"]
    assert summary["process_exit_code"] is None
